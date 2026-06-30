import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.deps import get_current_user
from app.models.user import User
from app.services.analysis import AnalysisService
from app.schemas.analysis import (
    AnalysisCreate, AnalysisResponse, AnalysisDetail,
    AnalysisListResponse, AnalysisStats,
)

router = APIRouter(prefix="/analyses", tags=["Analyses"])


@router.post("", response_model=AnalysisResponse, status_code=201,
             summary="Create a new analysis",
             description="Starts a new jurisprudential analysis for 1-5 uploaded documents. "
                         "Processing runs asynchronously in the background through a 5-step pipeline: "
                         "(1) PDF reading, (2) entity extraction, (3) mind map construction, "
                         "(4) explanation generation, (5) source linking. "
                         "Progress updates are sent via WebSocket to `ws://host/ws/{user_id}`.")
async def create_analysis(
    data: AnalysisCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = AnalysisService(db)
    analysis = await service.create(current_user.id, data)
    return AnalysisResponse(
        id=analysis.id, title=analysis.title, status=analysis.status,
        processing_step=analysis.processing_step, created_at=analysis.created_at,
        updated_at=analysis.updated_at,
    )


@router.get("", response_model=AnalysisListResponse,
            summary="List user analyses",
            description="Returns a paginated list of the authenticated user's analyses, "
                        "ordered by most recently updated. Supports text search by title.")
async def list_analyses(
    page: int = Query(1, ge=1, description="Page number (starts at 1)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    search: str | None = Query(None, description="Search analyses by title (case-insensitive)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = AnalysisService(db)

    def to_response(a):
        return AnalysisResponse(
            id=a.id, title=a.title, status=a.status,
            processing_step=a.processing_step, created_at=a.created_at, updated_at=a.updated_at,
            document_count=len(a.document_links) if a.document_links is not None else 0,
        )

    if search:
        items = await service.search(current_user.id, search)
        return AnalysisListResponse(items=[to_response(a) for a in items],
                                    total=len(items), page=1, page_size=len(items))

    items, total = await service.get_history(current_user.id, page, page_size)
    return AnalysisListResponse(items=[to_response(a) for a in items],
                                total=total, page=page, page_size=page_size)


@router.get("/stats", response_model=AnalysisStats,
            summary="Get analysis statistics",
            description="Returns count of analyses grouped by status: total, completed, processing, and failed.")
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = AnalysisService(db)
    stats = await service.get_stats(current_user.id)
    return AnalysisStats(**stats)


@router.get("/{analysis_id}", response_model=AnalysisDetail,
            summary="Get analysis detail",
            description="Returns the full analysis including associated documents, "
                        "classified findings (fundamentos), mind map data, parties, "
                        "background, and ruling. Only accessible by the analysis owner.")
async def get_analysis(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = AnalysisService(db)
    analysis = await service.get_detail(analysis_id, current_user.id)

    documents = [doc_link.document for doc_link in analysis.document_links]
    node_count = len((analysis.mind_map_data or {}).get("nodes", []))

    return AnalysisDetail(
        id=analysis.id, title=analysis.title, status=analysis.status,
        processing_step=analysis.processing_step, custom_prompt=analysis.custom_prompt,
        parties=analysis.parties, background=analysis.background, ruling=analysis.ruling,
        mind_map_data=analysis.mind_map_data, error_message=analysis.error_message,
        created_at=analysis.created_at, updated_at=analysis.updated_at,
        document_count=len(documents), node_count=node_count,
        documents=documents, findings=analysis.findings,
    )


@router.delete("/{analysis_id}", status_code=204,
               summary="Delete an analysis",
               description="Permanently deletes the analysis and all related data "
                           "(findings, mind map, document links) via CASCADE. This action is irreversible.")
async def delete_analysis(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = AnalysisService(db)
    await service.delete(analysis_id, current_user.id)


@router.post("/{analysis_id}/cancel", status_code=200,
             summary="Cancel a running analysis",
             description="Cancels an analysis that is currently in 'processing' status. "
                         "Cannot cancel analyses that are already completed or failed.")
async def cancel_analysis(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = AnalysisService(db)
    await service.cancel(analysis_id, current_user.id)
    return {"message": "Análisis cancelado"}
