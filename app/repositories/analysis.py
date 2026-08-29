import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis import Analysis, AnalysisDocument
from app.models.fundamento import AnalysisFundamento
from app.repositories.base import BaseRepository


class AnalysisRepository(BaseRepository[Analysis]):
    def __init__(self, db: AsyncSession):
        super().__init__(Analysis, db)

    async def get_by_user(self, user_id: uuid.UUID, page: int = 1, page_size: int = 20) -> tuple[list[Analysis], int]:
        total = await self.count(Analysis.user_id == user_id)

        query = (
            select(Analysis)
            .options(selectinload(Analysis.document_links))
            .where(Analysis.user_id == user_id)
            .order_by(Analysis.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.db.execute(query)
        return result.scalars().all(), total

    async def get_detail(self, analysis_id: uuid.UUID) -> Analysis | None:
        query = (
            select(Analysis)
            .options(
                selectinload(Analysis.document_links).selectinload(AnalysisDocument.document),
                selectinload(Analysis.findings),
            )
            .where(Analysis.id == analysis_id)
        )
        result = await self.db.execute(query)
        return result.scalar_one_or_none()

    async def update_status(self, analysis_id: uuid.UUID, status: str, step: int | None = None, error: str | None = None) -> None:
        analysis = await self.get_by_id(analysis_id)
        if analysis:
            analysis.status = status
            if step is not None:
                analysis.processing_step = step
            if error is not None:
                analysis.error_message = error
            await self.db.commit()

    async def get_status(self, analysis_id: uuid.UUID) -> str | None:
        # Lectura fresca del estado (select de columna, sin caché de la identity map),
        # para que el pipeline pueda detectar una cancelación hecha en otra sesión.
        result = await self.db.execute(select(Analysis.status).where(Analysis.id == analysis_id))
        return result.scalar_one_or_none()

    async def update_mindmap(self, analysis_id: uuid.UUID, data: dict) -> None:
        analysis = await self.get_by_id(analysis_id)
        if analysis:
            analysis.mind_map_data = data
            await self.db.commit()

    async def update_analysis_results(self, analysis_id: uuid.UUID, mind_map_data: dict, parties: dict, background: str) -> None:
        analysis = await self.get_by_id(analysis_id)
        if analysis:
            analysis.mind_map_data = mind_map_data
            analysis.parties = parties
            analysis.background = background
            await self.db.commit()

    async def save_fundamentos(self, fundamentos: list[AnalysisFundamento]) -> None:
        self.db.add_all(fundamentos)
        await self.db.commit()

    async def get_stats(self, user_id: uuid.UUID) -> dict:
        base = select(func.count()).select_from(Analysis).where(Analysis.user_id == user_id)

        total = (await self.db.execute(base)).scalar_one()
        completed = (await self.db.execute(base.where(Analysis.status == "completed"))).scalar_one()
        processing = (await self.db.execute(base.where(Analysis.status == "processing"))).scalar_one()
        failed = (await self.db.execute(base.where(Analysis.status == "failed"))).scalar_one()

        from app.models.document import Document
        doc_count = (await self.db.execute(
            select(func.count()).select_from(Document).where(Document.user_id == user_id)
        )).scalar_one()

        node_count = 0
        node_result = await self.db.execute(
            select(Analysis.mind_map_data)
            .where(Analysis.user_id == user_id, Analysis.status == "completed", Analysis.mind_map_data.isnot(None))
        )
        for row in node_result.scalars().all():
            if isinstance(row, dict):
                node_count += len(row.get("nodes", []))

        return {
            "total": total,
            "total_analyses": total,
            "completed": completed,
            "processing": processing,
            "failed": failed,
            "documents": doc_count,
            "nodes": node_count,
        }

    async def search(self, user_id: uuid.UUID, query: str) -> list[Analysis]:
        result = await self.db.execute(
            select(Analysis)
            .options(selectinload(Analysis.document_links))
            .where(Analysis.user_id == user_id, Analysis.title.ilike(f"%{query}%"))
            .order_by(Analysis.created_at.desc())
            .limit(50)
        )
        return result.scalars().all()
