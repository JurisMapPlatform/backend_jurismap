import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.document import DocumentResponse
from app.schemas.fundamento import FundamentoResponse


class AnalysisCreate(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    title: str | None = Field(default=None, max_length=500)
    custom_prompt: str | None = None


class AnalysisResponse(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    processing_step: int
    created_at: datetime
    updated_at: datetime
    document_count: int = 0

    model_config = {"from_attributes": True}


class AnalysisDetail(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    processing_step: int
    custom_prompt: str | None
    parties: dict | None
    background: str | None
    ruling: str | None
    mind_map_data: dict | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    document_count: int = 0
    node_count: int = 0
    documents: list[DocumentResponse] = []
    findings: list[FundamentoResponse] = []

    model_config = {"from_attributes": True}


class AnalysisListResponse(BaseModel):
    items: list[AnalysisResponse]
    total: int
    page: int
    page_size: int


class AnalysisStats(BaseModel):
    total: int = 0
    total_analyses: int = 0
    completed: int = 0
    processing: int = 0
    failed: int = 0
    documents: int = 0
    nodes: int = 0


class ProcessingUpdate(BaseModel):
    analysis_id: str
    step: int
    step_name: str
    status: str
    progress: float | None = None
