import uuid
from datetime import datetime

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: uuid.UUID
    original_filename: str
    file_size_bytes: int
    page_count: int | None
    expediente: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class DocumentValidation(BaseModel):
    is_valid: bool
    page_count: int | None = None
    error: str | None = None


class UploadResponse(BaseModel):
    document: DocumentResponse
    validation: DocumentValidation
