import uuid
import asyncio
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import Analysis, AnalysisDocument
from app.models.fundamento import AnalysisFundamento
from app.repositories.analysis import AnalysisRepository
from app.repositories.document import DocumentRepository
from app.services.ws import ws_manager
from app.schemas.analysis import AnalysisCreate

PROCESSING_STEPS = [
    "Lectura de documentos",
    "Clasificación con BETO",
    "Análisis con Gemini",
    "Construcción del mapa mental",
    "Generación de explicaciones",
]


class AnalysisService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.analysis_repo = AnalysisRepository(db)
        self.document_repo = DocumentRepository(db)

    async def create(self, user_id: uuid.UUID, data: AnalysisCreate) -> Analysis:
        documents = await self.document_repo.get_by_ids(data.document_ids, user_id)
        if len(documents) != len(data.document_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uno o más documentos no encontrados")

        analysis = Analysis(
            user_id=user_id,
            title=data.title or f"Análisis {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            custom_prompt=data.custom_prompt,
            status="pending",
        )
        analysis = await self.analysis_repo.create(analysis)

        for doc in documents:
            link = AnalysisDocument(analysis_id=analysis.id, document_id=doc.id)
            self.db.add(link)
        await self.db.commit()

        asyncio.create_task(self._process(analysis.id, str(user_id)))
        return analysis

    async def _notify(self, user_id: str, analysis_id: uuid.UUID, step: int, step_status: str = "processing"):
        await ws_manager.send_progress(user_id, {
            "analysis_id": str(analysis_id),
            "step": step,
            "step_name": PROCESSING_STEPS[step - 1] if step <= len(PROCESSING_STEPS) else "Completado",
            "status": step_status,
            "total_steps": len(PROCESSING_STEPS),
        })

    async def _process(self, analysis_id: uuid.UUID, user_id: str):
        from app.database import async_session
        from app.ai.extractor import PDFExtractor
        from app.ai.classifier import get_classifier
        from app.ai.gemini import GeminiClient
        from app.repositories.storage import StorageRepository

        async with async_session() as db:
            repo = AnalysisRepository(db)
            doc_repo = DocumentRepository(db)

            try:
                await repo.update_status(analysis_id, "processing", step=0)

                # --- Paso 1: Lectura de documentos ---
                await self._notify(user_id, analysis_id, 1)
                await repo.update_status(analysis_id, "processing", step=1)

                analysis = await repo.get_detail(analysis_id)
                doc_ids = [link.document_id for link in analysis.document_links]
                storage = StorageRepository()
                extractor = PDFExtractor()

                all_fundamentos = []
                full_text = ""
                for doc_id in doc_ids:
                    doc = await doc_repo.get_by_id(doc_id)
                    if not doc:
                        continue
                    pdf_bytes = await storage.download(doc.storage_path)
                    text = await asyncio.to_thread(extractor.extract_text, pdf_bytes)
                    full_text += text + "\n"
                    fundamentos = await asyncio.to_thread(extractor.extract_fundamentos, pdf_bytes)
                    for f in fundamentos:
                        f["document_id"] = str(doc_id)
                    all_fundamentos.extend(fundamentos)

                if not all_fundamentos:
                    raise ValueError("No se encontraron fundamentos en los documentos")

                # --- Paso 2: Clasificación con BETO ---
                await self._notify(user_id, analysis_id, 2)
                await repo.update_status(analysis_id, "processing", step=2)

                def _classify():
                    return get_classifier().classify_fundamentos(all_fundamentos)
                try:
                    all_fundamentos = await asyncio.to_thread(_classify)
                except FileNotFoundError:
                    for f in all_fundamentos:
                        f["beto_label"] = "RELEVANTE"
                        f["beto_confidence"] = 0.5

                # --- Paso 3: Análisis con Gemini ---
                await self._notify(user_id, analysis_id, 3)
                await repo.update_status(analysis_id, "processing", step=3)

                gemini = GeminiClient()
                selected = await asyncio.to_thread(gemini.analyze_fundamentos, all_fundamentos)

                parties = await asyncio.to_thread(gemini.extract_parties, full_text[:3000])

                # --- Paso 4: Construcción del mapa mental ---
                await self._notify(user_id, analysis_id, 4)
                await repo.update_status(analysis_id, "processing", step=4)

                selected_nums = {s["n"] for s in selected}
                fundamentos_for_map = [f for f in all_fundamentos if f["fundamento_num"] in selected_nums]
                summaries = {s["n"]: s["summary"] for s in selected}

                # El fallo (parte resolutiva) va al final de la sentencia y no es un fundamento
                # numerado; se pasa aparte para que Gemini no lo infiera. Va temprano en el dict
                # para que sobreviva al recorte de contexto.
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

                # --- Paso 5: Generación de explicaciones ---
                # Las explicaciones simplificadas ya vienen en metadata.summary desde build_mindmap
                # (paso 4). Se eliminó la llamada por-nodo a gemini.simplify() porque disparaba una
                # petición extra a Gemini por cada fundamento, agotando la cuota tras pocos análisis.
                await self._notify(user_id, analysis_id, 5)
                await repo.update_status(analysis_id, "processing", step=5)

                # Persistir la clasificación de cada fundamento (label/confianza de RoBERTalex,
                # si fue seleccionado, y su explicación). Alimenta el detalle del análisis y el PDF.
                findings = [
                    AnalysisFundamento(
                        analysis_id=analysis_id,
                        document_id=uuid.UUID(f["document_id"]),
                        fundamento_num=f["fundamento_num"],
                        texto=f["texto"],
                        label=f.get("beto_label", "RELEVANTE"),
                        confidence=float(f.get("beto_confidence", 0.5)),
                        is_selected=f["fundamento_num"] in selected_nums,
                        simplified_text=summaries.get(f["fundamento_num"]) if f["fundamento_num"] in selected_nums else None,
                    )
                    for f in all_fundamentos
                ]
                await repo.save_fundamentos(findings)

                # --- Guardar resultados ---
                await repo.update_analysis_results(
                    analysis_id,
                    mind_map_data=mind_map,
                    parties=parties,
                    background=full_text[:2000],
                )
                await repo.update_status(analysis_id, "completed", step=len(PROCESSING_STEPS))
                await self._notify(user_id, analysis_id, len(PROCESSING_STEPS), "completed")

            except Exception as e:
                await repo.update_status(analysis_id, "failed", error=str(e))
                await ws_manager.send_progress(user_id, {
                    "analysis_id": str(analysis_id),
                    "status": "failed",
                    "error": str(e),
                })

    async def get_history(self, user_id: uuid.UUID, page: int = 1, page_size: int = 20):
        return await self.analysis_repo.get_by_user(user_id, page, page_size)

    async def get_detail(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> Analysis:
        analysis = await self.analysis_repo.get_detail(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Análisis no encontrado")
        return analysis

    async def rename(self, analysis_id: uuid.UUID, user_id: uuid.UUID, title: str) -> Analysis:
        analysis = await self.analysis_repo.get_by_id(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Análisis no encontrado")
        analysis.title = title
        await self.analysis_repo.update(analysis)
        return analysis

    async def delete(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> None:
        analysis = await self.analysis_repo.get_by_id(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Análisis no encontrado")
        await self.analysis_repo.delete(analysis)

    async def cancel(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> None:
        analysis = await self.analysis_repo.get_by_id(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Análisis no encontrado")
        if analysis.status != "processing":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Solo se puede cancelar un análisis en proceso")
        await self.analysis_repo.update_status(analysis_id, "cancelled")

    async def get_stats(self, user_id: uuid.UUID) -> dict:
        return await self.analysis_repo.get_stats(user_id)

    async def search(self, user_id: uuid.UUID, query: str) -> list[Analysis]:
        return await self.analysis_repo.search(user_id, query)
