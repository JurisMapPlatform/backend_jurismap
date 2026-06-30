import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.deps import get_current_user
from app.models.user import User
from app.services.mindmap import MindMapService
from app.schemas.mindmap import (
    NodeData, GenerateNodeRequest, GenerateNodeResponse, RenameNodeRequest,
    DeleteNodeRequest, AutoSaveRequest, MindMapData,
)

router = APIRouter(prefix="/mindmap", tags=["Mind Map"])


@router.post("/{analysis_id}/generate-node", response_model=GenerateNodeResponse,
             summary="Generate a new node with AI",
             description="Uses Gemini to generate a new mind map node based on the user's prompt "
                         "and the existing map context. The new node is added as a child of the "
                         "specified parent node (or the central node if none is given) with "
                         "`source: 'ai_generated'`. Returns the created node and edge.")
async def generate_node(
    analysis_id: uuid.UUID,
    request: GenerateNodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = MindMapService(db)
    return await service.generate_node(analysis_id, current_user.id, request)


@router.patch("/{analysis_id}/rename-node",
              summary="Rename a node",
              description="Changes the label of an existing node in the mind map. "
                         "Works for both system-generated and AI-generated nodes.")
async def rename_node(
    analysis_id: uuid.UUID,
    request: RenameNodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = MindMapService(db)
    await service.rename_node(analysis_id, current_user.id, request.node_id, request.new_label)
    return {"message": "Nodo renombrado"}


@router.delete("/{analysis_id}/node",
               summary="Delete a node and its subtree",
               description="Removes a node and all its descendant nodes from the mind map. "
                          "Associated edges are also removed. Cannot delete the central node.")
async def delete_node(
    analysis_id: uuid.UUID,
    request: DeleteNodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = MindMapService(db)
    await service.delete_node(analysis_id, current_user.id, request.node_id)
    return {"message": "Nodo eliminado"}


@router.put("/{analysis_id}/auto-save",
            summary="Auto-save mind map state",
            description="Saves the current state of the mind map including node positions, "
                       "manual edits, and layout changes. Called automatically by the frontend.")
async def auto_save(
    analysis_id: uuid.UUID,
    data: AutoSaveRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = MindMapService(db)
    await service.auto_save(analysis_id, current_user.id, {"nodes": data.nodes, "edges": data.edges})
    return {"message": "Mapa guardado"}


@router.post("/{analysis_id}/regenerate",
             summary="Regenerate mind map",
             description="Re-processes the analysis documents and regenerates the mind map from scratch.")
async def regenerate(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = MindMapService(db)
    data = await service.regenerate(analysis_id, current_user.id)
    return data


@router.post("/{analysis_id}/reorganize", response_model=MindMapData,
             summary="Reorganize mind map with AI",
             description="Uses Gemini to automatically reorganize the mind map structure "
                        "for better readability and logical flow.")
async def reorganize(
    analysis_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = MindMapService(db)
    data = await service.reorganize(analysis_id, current_user.id)
    return MindMapData(**data)
