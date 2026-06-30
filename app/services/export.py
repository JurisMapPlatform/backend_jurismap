import uuid
import json

from fastapi import HTTPException, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.analysis import AnalysisRepository


class ExportService:
    def __init__(self, db: AsyncSession):
        self.repo = AnalysisRepository(db)

    async def _get_analysis(self, analysis_id: uuid.UUID, user_id: uuid.UUID):
        analysis = await self.repo.get_by_id(analysis_id)
        if not analysis or analysis.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Análisis no encontrado")
        if not analysis.mind_map_data:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El mapa mental aún no se ha generado")
        return analysis

    async def export_json(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> Response:
        analysis = await self._get_analysis(analysis_id, user_id)
        content = json.dumps(analysis.mind_map_data, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{analysis.title}.json"'},
        )

    async def export_image(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> Response:
        analysis = await self._get_analysis(analysis_id, user_id)
        # TODO: renderizar mapa mental como PNG con Playwright o similar
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Exportación a imagen pendiente de implementar")

    async def export_pdf(self, analysis_id: uuid.UUID, user_id: uuid.UUID) -> Response:
        analysis = await self._get_analysis(analysis_id, user_id)
        # TODO: generar PDF con WeasyPrint
        raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Exportación a PDF pendiente de implementar")
