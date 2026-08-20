import asyncio
import uuid

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.analysis import AnalysisRepository
from app.schemas.mindmap import MindMapData, GenerateNodeRequest, NodeData, EdgeData


class MindMapService:
    def __init__(self, db: AsyncSession):
        self.repo = AnalysisRepository(db)

    async def _get_analysis(self, analysis_id: uuid.UUID, user_id: uuid.UUID):
        analysis = await self.repo.get_by_id(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Análisis no encontrado")
        return analysis

    async def generate_node(self, analysis_id: uuid.UUID, user_id: uuid.UUID, request: GenerateNodeRequest) -> dict:
        from app.ai.gemini import GeminiClient

        analysis = await self._get_analysis(analysis_id, user_id)
        if not analysis.mind_map_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mapa mental aún no se ha generado")

        mind_map = analysis.mind_map_data
        nodes = mind_map.get("nodes", [])

        parent_id = request.parent_node_id
        if not parent_id or not any(n["id"] == parent_id for n in nodes):
            root = next((n for n in nodes if n.get("type") == "central"), None)
            parent_id = root["id"] if root else (nodes[0]["id"] if nodes else "root")

        try:
            gen = await asyncio.to_thread(GeminiClient().generate_node, {"nodes": nodes}, request.prompt)
            label = (gen.get("label") or request.prompt)[:60]
            metadata = gen.get("metadata") or {}
        except Exception:
            label = request.prompt[:60]
            metadata = {}
        metadata["prompt"] = request.prompt

        new_node = NodeData(
            id=str(uuid.uuid4()),
            type="ai_generated",
            label=label,
            source="ai_generated",
            metadata=metadata,
        )
        new_edge = EdgeData(source=parent_id, target=new_node.id)

        nodes.append(new_node.model_dump())
        mind_map.setdefault("edges", []).append(new_edge.model_dump())
        await self.repo.update_mindmap(analysis_id, mind_map)

        return {"node": new_node.model_dump(), "edge": new_edge.model_dump()}

    async def rename_node(self, analysis_id: uuid.UUID, user_id: uuid.UUID, node_id: str, new_label: str) -> None:
        analysis = await self._get_analysis(analysis_id, user_id)
        if not analysis.mind_map_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mapa mental aún no se ha generado")

        mind_map = analysis.mind_map_data
        for node in mind_map.get("nodes", []):
            if node["id"] == node_id:
                node["label"] = new_label
                await self.repo.update_mindmap(analysis_id, mind_map)
                return
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nodo no encontrado")

    async def delete_node(self, analysis_id: uuid.UUID, user_id: uuid.UUID, node_id: str) -> None:
        analysis = await self._get_analysis(analysis_id, user_id)
        if not analysis.mind_map_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mapa mental aún no se ha generado")

        mind_map = analysis.mind_map_data
        nodes_to_remove = self._collect_subtree(mind_map, node_id)
        mind_map["nodes"] = [n for n in mind_map["nodes"] if n["id"] not in nodes_to_remove]
        mind_map["edges"] = [e for e in mind_map["edges"] if e["source"] not in nodes_to_remove and e["target"] not in nodes_to_remove]
        await self.repo.update_mindmap(analysis_id, mind_map)

    def _collect_subtree(self, mind_map: dict, node_id: str) -> set[str]:
        to_remove = {node_id}
        changed = True
        while changed:
            changed = False
            for edge in mind_map.get("edges", []):
                if edge["source"] in to_remove and edge["target"] not in to_remove:
                    to_remove.add(edge["target"])
                    changed = True
        return to_remove

    async def auto_save(self, analysis_id: uuid.UUID, user_id: uuid.UUID, data: dict) -> None:
        if not data.get("nodes"):
            return
        await self._get_analysis(analysis_id, user_id)
        await self.repo.update_mindmap(analysis_id, data)

    async def regenerate(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        from app.repositories.document import DocumentRepository
        from app.repositories.storage import StorageRepository
        from app.ai.extractor import PDFExtractor
        from app.ai.classifier import get_classifier
        from app.ai.gemini import GeminiClient

        analysis = await self._get_analysis(analysis_id, user_id)
        detail = await self.repo.get_detail(analysis_id)

        doc_repo = DocumentRepository(self.repo.db)
        storage = StorageRepository()
        extractor = PDFExtractor()

        all_fundamentos = []
        full_text = ""
        for link in detail.document_links:
            doc = await doc_repo.get_by_id(link.document_id)
            if not doc:
                continue
            pdf_bytes = await storage.download(doc.storage_path)
            text = await asyncio.to_thread(extractor.extract_text, pdf_bytes)
            full_text += text + "\n"
            fundamentos = await asyncio.to_thread(extractor.extract_fundamentos, pdf_bytes)
            for f in fundamentos:
                f["document_id"] = str(doc.id)
            all_fundamentos.extend(fundamentos)

        if not all_fundamentos:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No se encontraron fundamentos")

        def _classify():
            return get_classifier().classify_fundamentos(all_fundamentos)
        try:
            all_fundamentos = await asyncio.to_thread(_classify)
        except FileNotFoundError:
            for f in all_fundamentos:
                f["beto_label"] = "RELEVANTE"
                f["beto_confidence"] = 0.5

        gemini = GeminiClient()
        selected = await asyncio.to_thread(gemini.analyze_fundamentos, all_fundamentos)
        parties = await asyncio.to_thread(gemini.extract_parties, full_text[:3000])

        selected_nums = {s["n"] for s in selected}
        fundamentos_for_map = [f for f in all_fundamentos if f["fundamento_num"] in selected_nums]
        summaries = {s["n"]: s["summary"] for s in selected}

        fallo_text = extractor.extract_fallo(full_text)

        analysis_data = {
            "expediente": analysis.title,
            "parties": parties,
            "fallo_text": fallo_text,
            "full_text_preview": full_text[:2000],
            "fundamentos": [
                {
                    "num": f["fundamento_num"],
                    "texto": f["texto"][:800],
                    "summary": summaries.get(f["fundamento_num"], ""),
                    "beto_label": f.get("beto_label"),
                }
                for f in fundamentos_for_map
            ],
        }
        mind_map = await asyncio.to_thread(gemini.build_mindmap, analysis_data, analysis.custom_prompt)

        # Las explicaciones simplificadas ya vienen en metadata.summary desde build_mindmap;
        # se eliminó la llamada por-nodo a gemini.simplify() para no agotar la cuota de Gemini.

        await self.repo.update_analysis_results(analysis_id, mind_map_data=mind_map, parties=parties, background=full_text[:2000])
        return mind_map

    async def reorganize(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        analysis = await self._get_analysis(analysis_id, user_id)
        if not analysis.mind_map_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mapa mental aún no se ha generado")
        return analysis.mind_map_data
