import json
import logging
import re
import uuid
import asyncio
from datetime import datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.analysis import Analysis, AnalysisDocument
from app.models.fundamento import AnalysisFundamento
from app.repositories.analysis import AnalysisRepository
from app.repositories.document import DocumentRepository
from app.services.ws import ws_manager
from app.schemas.analysis import AnalysisCreate

logger = logging.getLogger(__name__)


def friendly_error(exc: Exception) -> str:
    """HU-32: traduce una excepción del pipeline a un mensaje claro para el estudiante, con una
    sugerencia de qué hacer. El detalle técnico se registra en los logs, no se muestra al usuario."""
    text = str(exc)
    low = text.lower()
    if isinstance(exc, ValueError) and "fundamentos" in low:
        return ("No se encontraron fundamentos numerados en los documentos. Verifica que sean sentencias "
                "del Tribunal Constitucional con texto seleccionable e inténtalo de nuevo.")
    if isinstance(exc, json.JSONDecodeError) or "expecting value" in low or "json" in low:
        return "La IA devolvió una respuesta incompleta. Vuelve a intentar el análisis en unos minutos."
    if "429" in text or "resource exhausted" in low or "quota" in low or "max retries" in low:
        return "El servicio de IA alcanzó su límite de uso por ahora. Espera unos minutos y vuelve a intentarlo."
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
        k in low for k in ("timeout", "timed out", "deadline", "unavailable", "connection")
    ):
        return "No se pudo conectar con el servicio de IA. Vuelve a intentarlo en unos minutos."
    return ("Ocurrió un error inesperado al procesar el análisis. Vuelve a intentarlo; "
            "si el problema continúa, prueba con otro documento.")


# Los números de fundamento se repiten: dentro de una sentencia (antecedentes, fundamentos y puntos
# del fallo se numeran por separado) y entre sentencias. Por eso cada bloque extraído lleva una
# referencia única "documento-número" (ref) y la sección donde aparece. El clasificador recibe
# TODOS los bloques, igual que en su entrenamiento; después solo los de la sección de fundamentos
# pasan a Gemini y al mapa.

def tag_fundamentos(fundamentos: list[dict], doc, doc_index: int) -> None:
    for f in fundamentos:
        f["document_id"] = str(doc.id)
        f["document_name"] = doc.original_filename
        f["doc_index"] = doc_index
        f["ref"] = f"{doc_index}-{f['fundamento_num']}"


def select_candidates(fundamentos: list[dict]) -> list[dict]:
    """Bloques que pueden llegar a Gemini y al mapa: los de la sección de fundamentos de cada
    documento (todos los suyos si un documento no la tiene). Si una referencia se repite dentro
    del documento (p. ej. dos sentencias en un mismo PDF), se queda el de mayor confianza."""
    docs_con_seccion = {f["doc_index"] for f in fundamentos if f.get("section") == "fundamentos"}
    best: dict[str, dict] = {}
    for f in fundamentos:
        if f["doc_index"] in docs_con_seccion and f.get("section") != "fundamentos":
            continue
        actual = best.get(f["ref"])
        if actual is None or f.get("beto_confidence", 0) > actual.get("beto_confidence", 0):
            best[f["ref"]] = f
    elegidos = {id(f) for f in best.values()}
    return [f for f in fundamentos if id(f) in elegidos]


def prepare_map_fundamentos(candidates: list[dict], selected: list[dict]) -> tuple[list[dict], dict]:
    """Fundamentos del mapa (en el orden de la sentencia) y sus resúmenes por referencia."""
    summaries = {s["ref"]: s.get("summary", "") for s in selected}
    return [f for f in candidates if f["ref"] in summaries], summaries


def map_payload(fundamentos_for_map: list[dict], summaries: dict) -> list[dict]:
    return [
        {
            "ref": f["ref"],
            "num": f["fundamento_num"],
            "documento": f["doc_index"],
            "texto": f["texto"][:800],
            "summary": summaries.get(f["ref"], ""),
            "beto_label": f.get("beto_label"),
        }
        for f in fundamentos_for_map
    ]


def add_missing_fundamentos(mind_map: dict, fundamentos_for_map: list[dict], summaries: dict) -> dict:
    """Gemini a veces omite al armar el mapa algunos de los fundamentos elegidos (p. ej. todos los
    del segundo documento). En el modo predeterminado se agregan los que falten bajo la categoría
    Fundamentos, con su resumen y su texto original, para que el mapa muestre todos los elegidos."""
    if not mind_map:
        return mind_map
    nodes = mind_map.setdefault("nodes", [])
    categoria = next((n for n in nodes if n.get("type") == "category" and (
        "fundamento" in str(n.get("id", "")).lower() or "fundamento" in str(n.get("label", "")).lower())), None)
    if categoria is None:
        return mind_map

    # Un nodo sin ref se enlaza por número al primer fundamento con ese número (igual que en
    # enrich_fundamento_nodes), así que ese fundamento cuenta como presente.
    by_num: dict[int, str] = {}
    for f in fundamentos_for_map:
        by_num.setdefault(f["fundamento_num"], f["ref"])
    presentes = set()
    for n in nodes:
        md = n.get("metadata") or {}
        if md.get("fundamento_ref"):
            presentes.add(str(md["fundamento_ref"]).strip())
        else:
            try:
                presentes.add(by_num.get(int(md.get("fundamento_num"))))
            except (TypeError, ValueError):
                pass

    edges = mind_map.setdefault("edges", [])
    for f in fundamentos_for_map:
        if f["ref"] in presentes:
            continue
        node_id = "fund_" + f["ref"].replace("-", "_")
        nodes.append({
            "id": node_id,
            "type": "fundamento",
            "label": f"Fund. {f['fundamento_num']}",
            "metadata": {"fundamento_ref": f["ref"], "fundamento_num": f["fundamento_num"],
                         "summary": summaries.get(f["ref"], ""), "original": f["texto"]},
        })
        edges.append({"source": categoria["id"], "target": node_id})
    return mind_map


_FUND_LABEL = re.compile(r"^\s*Fund(?:amento)?\.?\s*\d+\s*$", re.IGNORECASE)


def enrich_fundamento_nodes(mind_map: dict, fundamentos: list[dict], multi_doc: bool = False) -> dict:
    """HU-12: enlaza cada nodo de fundamento con el bloque exacto del que proviene (finding_id,
    documento y página), para que el modal muestre el texto y el origen correctos aunque el número
    se repita. Con varios documentos, la etiqueta indica el documento ("Fund. 7 · Doc. 2")."""
    by_ref = {f["ref"]: f for f in fundamentos if f.get("ref")}
    by_num: dict[int, dict] = {}
    for f in fundamentos:
        by_num.setdefault(f["fundamento_num"], f)
    for node in (mind_map or {}).get("nodes", []):
        metadata = node.get("metadata") or {}
        source = by_ref.get(str(metadata.get("fundamento_ref", "")).strip())
        if source is None:
            try:
                source = by_num.get(int(metadata.get("fundamento_num")))
            except (TypeError, ValueError):
                continue
        if not source:
            continue
        metadata["fundamento_num"] = source["fundamento_num"]
        metadata["document_id"] = source.get("document_id")
        metadata["document_name"] = source.get("document_name")
        if source.get("ref"):
            metadata["fundamento_ref"] = source["ref"]
        if source.get("finding_id"):
            metadata["finding_id"] = str(source["finding_id"])
        if source.get("page_number"):
            metadata["page_number"] = source["page_number"]
        node["metadata"] = metadata
        if multi_doc and source.get("doc_index") and _FUND_LABEL.match(node.get("label") or ""):
            node["label"] = f"Fund. {source['fundamento_num']} · Doc. {source['doc_index']}"
    _ensure_unique_node_ids(mind_map or {})
    return mind_map


def _ensure_unique_node_ids(mind_map: dict) -> None:
    """Si Gemini repite el id de un nodo (p. ej. "fund_7" en dos documentos), React Flow mezcla los
    nodos. Se renombran los repetidos y se conectan al mismo padre que el original."""
    edges = mind_map.setdefault("edges", [])
    vistos: set[str] = set()
    for node in mind_map.get("nodes", []):
        nid = node.get("id")
        if nid not in vistos:
            vistos.add(nid)
            continue
        k = 2
        while f"{nid}_{k}" in vistos:
            k += 1
        node["id"] = f"{nid}_{k}"
        vistos.add(node["id"])
        padre = next((e.get("source") for e in edges if e.get("target") == nid), None)
        if padre:
            edges.append({"source": padre, "target": node["id"]})


PROCESSING_STEPS = [
    "Lectura de documentos",
    "Clasificación con BETO",
    "Análisis con Gemini",
    "Construcción del mapa mental",
    "Generación de explicaciones",
]

# El pipeline corre como tarea en segundo plano dentro de la instancia. Si Cloud Run la recicla
# o se despliega una nueva revisión, la tarea muere sin pasar por el except y el análisis quedaría
# en "processing" para siempre. Para detectarlo, cada análisis vivo actualiza su updated_at
# periódicamente (latido) y un barrido marca como fallidos los que dejaron de latir.
ACTIVE_STATUSES = ("pending", "processing")
INTERRUPTED_MSG = ("El análisis se interrumpió porque el servidor se reinició. Vuelve a intentarlo; "
                   "tus documentos siguen guardados.")

# Análisis que se procesan en ESTA instancia, para marcarlos como fallidos si se apaga.
RUNNING_ANALYSES: set[uuid.UUID] = set()
# asyncio solo guarda referencias débiles a las tareas: sin esto, una tarea podría ser
# recolectada por el garbage collector a mitad del análisis.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


async def _heartbeat(analysis_id: uuid.UUID) -> None:
    from app.database import async_session

    while True:
        await asyncio.sleep(settings.analysis_heartbeat_seconds)
        try:
            async with async_session() as db:
                await db.execute(
                    update(Analysis)
                    .where(Analysis.id == analysis_id, Analysis.status.in_(ACTIVE_STATUSES))
                    .values(updated_at=func.now())
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
        except Exception:
            logger.warning("No se pudo registrar el latido del análisis %s", analysis_id, exc_info=True)


async def _mark_interrupted(condition) -> int:
    """Marca como fallidos los análisis activos que cumplen `condition` y avisa a sus dueños."""
    from app.database import async_session

    async with async_session() as db:
        result = await db.execute(
            update(Analysis)
            .where(Analysis.status.in_(ACTIVE_STATUSES), condition)
            .values(status="failed", error_message=INTERRUPTED_MSG)
            .returning(Analysis.id, Analysis.user_id)
            .execution_options(synchronize_session=False)
        )
        rows = result.all()
        await db.commit()
    for analysis_id, user_id in rows:
        logger.warning("El análisis %s se interrumpió; se marcó como fallido", analysis_id)
        await ws_manager.send_progress(str(user_id), {
            "analysis_id": str(analysis_id),
            "status": "failed",
            "error": INTERRUPTED_MSG,
        })
    return len(rows)


async def recover_stale_analyses(stale_minutes: int) -> int:
    """Barrido: falla los análisis activos cuyo latido no se actualiza hace `stale_minutes`."""
    return await _mark_interrupted(Analysis.updated_at < func.now() - timedelta(minutes=stale_minutes))


async def fail_running_analyses() -> int:
    """Al apagarse la instancia: falla los análisis que se estaban procesando en ella."""
    if not RUNNING_ANALYSES:
        return 0
    return await _mark_interrupted(Analysis.id.in_(list(RUNNING_ANALYSES)))


class AnalysisService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.analysis_repo = AnalysisRepository(db)
        self.document_repo = DocumentRepository(db)

    async def create(self, user_id: uuid.UUID, data: AnalysisCreate) -> Analysis:
        documents = await self.document_repo.get_by_ids(data.document_ids, user_id)
        if len(documents) != len(data.document_ids):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Uno o más documentos ya no están disponibles. Vuelve a subirlos e inténtalo de nuevo.")

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

        task = asyncio.create_task(self._process(analysis.id, str(user_id)))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return analysis

    async def _is_cancelled(self, repo, user_id: str, analysis_id: uuid.UUID) -> bool:
        """Cancelación cooperativa: si el usuario canceló el análisis, el pipeline se detiene
        en el siguiente punto de control y NO vuelve a marcarlo como 'processing'/'completed'."""
        if (await repo.get_status(analysis_id)) == "cancelled":
            await ws_manager.send_progress(user_id, {"analysis_id": str(analysis_id), "status": "cancelled"})
            return True
        return False

    async def _advance(self, repo, user_id: str, analysis_id: uuid.UUID, step: int) -> bool:
        """Pasa al paso `step` solo si el análisis sigue activo. Si el estudiante lo canceló (incluso
        estando pendiente), el pipeline se detiene sin volver a marcarlo como 'processing'."""
        if await repo.advance_step(analysis_id, step):
            return True
        await self._is_cancelled(repo, user_id, analysis_id)  # avisa por WebSocket si fue cancelado
        return False

    async def _notify(self, user_id: str, analysis_id: uuid.UUID, step: int, step_status: str = "processing"):
        await ws_manager.send_progress(user_id, {
            "analysis_id": str(analysis_id),
            "step": step,
            "step_name": PROCESSING_STEPS[step - 1] if step <= len(PROCESSING_STEPS) else "Completado",
            "status": step_status,
            "total_steps": len(PROCESSING_STEPS),
        })

    async def _process(self, analysis_id: uuid.UUID, user_id: str):
        RUNNING_ANALYSES.add(analysis_id)
        heartbeat = asyncio.create_task(_heartbeat(analysis_id))
        try:
            await self._run_pipeline(analysis_id, user_id)
        finally:
            heartbeat.cancel()
            RUNNING_ANALYSES.discard(analysis_id)

    async def _run_pipeline(self, analysis_id: uuid.UUID, user_id: str):
        from app.database import async_session
        from app.ai.extractor import PDFExtractor
        from app.ai.classifier import get_classifier
        from app.ai.gemini import GeminiClient
        from app.repositories.storage import StorageRepository

        async with async_session() as db:
            repo = AnalysisRepository(db)
            doc_repo = DocumentRepository(db)

            try:
                if not await self._advance(repo, user_id, analysis_id, 0):
                    return

                # --- Paso 1: Lectura de documentos ---
                if not await self._advance(repo, user_id, analysis_id, 1):
                    return
                await self._notify(user_id, analysis_id, 1)

                analysis = await repo.get_detail(analysis_id)
                doc_ids = [link.document_id for link in analysis.document_links]
                storage = StorageRepository()
                extractor = PDFExtractor()

                all_fundamentos = []
                full_text = ""
                doc_index = 0
                for doc_id in doc_ids:
                    doc = await doc_repo.get_by_id(doc_id)
                    if not doc:
                        continue
                    doc_index += 1
                    pdf_bytes = await storage.download(doc.storage_path)
                    text = await asyncio.to_thread(extractor.extract_text, pdf_bytes)
                    full_text += text + "\n"
                    fundamentos = await asyncio.to_thread(extractor.extract_fundamentos, pdf_bytes)
                    # HU-12: página de cada fundamento y documento del que proviene.
                    await asyncio.to_thread(extractor.assign_pages, pdf_bytes, fundamentos)
                    tag_fundamentos(fundamentos, doc, doc_index)
                    all_fundamentos.extend(fundamentos)

                if not all_fundamentos:
                    raise ValueError("No se encontraron fundamentos en los documentos")

                if await self._is_cancelled(repo, user_id, analysis_id):
                    return

                # --- Paso 2: Clasificación con BETO ---
                if not await self._advance(repo, user_id, analysis_id, 2):
                    return
                await self._notify(user_id, analysis_id, 2)

                def _classify():
                    return get_classifier().classify_fundamentos(all_fundamentos)
                try:
                    all_fundamentos = await asyncio.to_thread(_classify)
                except FileNotFoundError:
                    for f in all_fundamentos:
                        f["beto_label"] = "RELEVANTE"
                        f["beto_confidence"] = 0.5

                if await self._is_cancelled(repo, user_id, analysis_id):
                    return

                # --- Paso 3: Análisis con Gemini ---
                if not await self._advance(repo, user_id, analysis_id, 3):
                    return
                await self._notify(user_id, analysis_id, 3)

                gemini = GeminiClient()
                candidates = select_candidates(all_fundamentos)
                selected = await asyncio.to_thread(gemini.analyze_fundamentos, candidates)

                parties = await asyncio.to_thread(gemini.extract_parties, full_text[:3000])

                if await self._is_cancelled(repo, user_id, analysis_id):
                    return

                # --- Paso 4: Construcción del mapa mental ---
                if not await self._advance(repo, user_id, analysis_id, 4):
                    return
                await self._notify(user_id, analysis_id, 4)

                fundamentos_for_map, summaries = prepare_map_fundamentos(candidates, selected)
                chosen = {id(f) for f in fundamentos_for_map}
                multi_doc = doc_index > 1
                # El id de cada registro se fija ahora para enlazar cada nodo con su fundamento exacto.
                for f in all_fundamentos:
                    f["finding_id"] = uuid.uuid4()

                # El fallo (parte resolutiva) va al final de la sentencia y no es un fundamento
                # numerado; se pasa aparte para que Gemini no lo infiera. Va temprano en el dict
                # para que sobreviva al recorte de contexto.
                fallo_text = extractor.extract_fallo(full_text)

                analysis_data = {
                    "expediente": analysis.title,
                    "parties": parties,
                    "fallo_text": fallo_text,
                    "full_text_preview": full_text[:2000],
                    "fundamentos": map_payload(fundamentos_for_map, summaries),
                }
                mind_map = await asyncio.to_thread(gemini.build_mindmap, analysis_data, analysis.custom_prompt)
                if not analysis.custom_prompt:
                    mind_map = add_missing_fundamentos(mind_map, fundamentos_for_map, summaries)
                mind_map = enrich_fundamento_nodes(mind_map, fundamentos_for_map, multi_doc)

                if await self._is_cancelled(repo, user_id, analysis_id):
                    return

                # --- Paso 5: Generación de explicaciones ---
                # Las explicaciones simplificadas ya vienen en metadata.summary desde build_mindmap
                # (paso 4). Se eliminó la llamada por-nodo a gemini.simplify() porque disparaba una
                # petición extra a Gemini por cada fundamento, agotando la cuota tras pocos análisis.
                if not await self._advance(repo, user_id, analysis_id, 5):
                    return
                await self._notify(user_id, analysis_id, 5)

                # Persistir la clasificación de cada fundamento (label/confianza de RoBERTalex,
                # si fue seleccionado, y su explicación). Alimenta el detalle del análisis y el PDF.
                findings = [
                    AnalysisFundamento(
                        id=f["finding_id"],
                        analysis_id=analysis_id,
                        document_id=uuid.UUID(f["document_id"]),
                        fundamento_num=f["fundamento_num"],
                        texto=f["texto"],
                        label=f.get("beto_label", "RELEVANTE"),
                        confidence=float(f.get("beto_confidence", 0.5)),
                        is_selected=id(f) in chosen,
                        simplified_text=summaries.get(f["ref"]) if id(f) in chosen else None,
                        page_number=f.get("page_number"),
                    )
                    for f in all_fundamentos
                ]
                await repo.save_fundamentos(findings)

                if await self._is_cancelled(repo, user_id, analysis_id):
                    return

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
                logger.exception("Falló el análisis %s", analysis_id)
                user_msg = friendly_error(e)
                await repo.update_status(analysis_id, "failed", error=user_msg)
                await ws_manager.send_progress(user_id, {
                    "analysis_id": str(analysis_id),
                    "status": "failed",
                    "error": user_msg,
                })

    async def get_history(self, user_id: uuid.UUID, page: int = 1, page_size: int = 20):
        return await self.analysis_repo.get_by_user(user_id, page, page_size)

    async def get_detail(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> Analysis:
        analysis = await self.analysis_repo.get_detail(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró el análisis. Es posible que se haya eliminado; revisa tu historial.")
        return analysis

    async def rename(self, analysis_id: uuid.UUID, user_id: uuid.UUID, title: str) -> Analysis:
        analysis = await self.analysis_repo.get_by_id(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró el análisis. Es posible que se haya eliminado; revisa tu historial.")
        analysis.title = title
        await self.analysis_repo.update(analysis)
        return analysis

    async def delete(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> None:
        analysis = await self.analysis_repo.get_by_id(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró el análisis. Es posible que se haya eliminado; revisa tu historial.")
        await self.analysis_repo.delete(analysis)

    async def cancel(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> None:
        analysis = await self.analysis_repo.get_by_id(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No se encontró el análisis. Es posible que se haya eliminado; revisa tu historial.")
        # También se puede cancelar mientras está pendiente (recién creado, antes del primer paso).
        if analysis.status not in ACTIVE_STATUSES:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Solo se puede cancelar un análisis que aún no termina. Es posible que ya haya terminado; revisa tu historial.")
        await self.analysis_repo.update_status(analysis_id, "cancelled")

    async def get_stats(self, user_id: uuid.UUID) -> dict:
        return await self.analysis_repo.get_stats(user_id)

    async def search(self, user_id: uuid.UUID, query: str) -> list[Analysis]:
        return await self.analysis_repo.search(user_id, query)
