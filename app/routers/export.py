import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.deps import get_current_user
from app.models.user import User
from app.services.export import ExportService

router = APIRouter(prefix="/export", tags=["Export"])


@router.get("/{analysis_id}/json",
            summary="Export mind map as JSON",
            description="Downloads the mind map data as a JSON file containing "
                       "nodes (with labels, types, metadata) and edges. "
                       "Compatible with React Flow for re-importing.",
            response_class=Response)
async def export_json(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = ExportService(db)
    return await service.export_json(analysis_id, current_user.id)


@router.get("/{analysis_id}/image",
            summary="Export mind map as PNG image",
            description="Generates and downloads a PNG screenshot of the mind map. "
                       "Renders the full map at high resolution for printing or sharing.",
            response_class=Response)
async def export_image(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = ExportService(db)
    return await service.export_image(analysis_id, current_user.id)


@router.get("/{analysis_id}/pdf",
            summary="Export analysis as PDF",
            description="Generates a PDF document containing the mind map visualization, "
                       "analysis details (parties, background, ruling), and classified findings "
                       "with source page references.",
            response_class=Response)
async def export_pdf(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = ExportService(db)
    return await service.export_pdf(analysis_id, current_user.id)
