import asyncio
import re
import uuid

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.deps import get_current_user
from app.models.user import User
from app.models.document import Document
from app.repositories.document import DocumentRepository
from app.repositories.storage import StorageRepository
from app.schemas.document import DocumentResponse, DocumentValidation, UploadResponse
from app.config import settings

router = APIRouter(prefix="/documents", tags=["Documents"])

PDF_MAGIC = b"%PDF"


def _get_extractor():
    from app.ai.extractor import PDFExtractor
    return PDFExtractor()


def _sanitize_filename(filename: str) -> str:
    name = re.sub(r"[^\w\s\-.]", "", filename)
    return name[:200] if name else "document.pdf"


@router.post("/upload", response_model=UploadResponse, status_code=201,
             summary="Upload a PDF document",
             description="Uploads a TC ruling PDF file. The file is validated for readability "
                         "(must contain extractable text, not scanned images) and stored in "
                         "Google Cloud Storage. Maximum file size: 20MB.")
async def upload_document(
    file: UploadFile = File(..., description="PDF file of a TC ruling"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Solo se permiten archivos PDF")

    content = await file.read()
    if not content[:4].startswith(PDF_MAGIC):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo no es un PDF válido")

    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.max_upload_size_mb:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"El archivo excede {settings.max_upload_size_mb}MB")

    safe_filename = _sanitize_filename(file.filename)

    extractor = _get_extractor()
    is_valid, page_count = await asyncio.to_thread(extractor.is_readable, content)
    validation = DocumentValidation(is_valid=is_valid, page_count=page_count)
    if not is_valid:
        validation.error = "El PDF no contiene texto extraíble (posiblemente escaneado sin OCR)"

    storage = StorageRepository()
    storage_path = await storage.upload(content, safe_filename, current_user.id)

    repo = DocumentRepository(db)
    doc = Document(
        user_id=current_user.id,
        original_filename=safe_filename,
        storage_path=storage_path,
        file_size_bytes=len(content),
        page_count=page_count,
    )
    doc = await repo.create(doc)

    return UploadResponse(document=DocumentResponse.model_validate(doc), validation=validation)


@router.post("/validate", response_model=DocumentValidation,
             summary="Validate a PDF without uploading",
             description="Checks if a PDF file is readable and contains extractable text. "
                        "Useful for client-side pre-validation before uploading. "
                        "Does not store the file.")
async def validate_document(
    file: UploadFile = File(..., description="PDF file to validate"),
    current_user: User = Depends(get_current_user),
):
    content = await file.read()
    ext = _get_extractor()
    is_valid, page_count = await asyncio.to_thread(ext.is_readable, content)
    validation = DocumentValidation(is_valid=is_valid, page_count=page_count)
    if not is_valid:
        validation.error = "El PDF no contiene texto extraíble"
    return validation


@router.delete("/{document_id}", status_code=204,
               summary="Delete a document",
               description="Permanently deletes the document record and its file from Cloud Storage. "
                          "If the document is linked to analyses, those links are removed via CASCADE.")
async def delete_document(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    repo = DocumentRepository(db)
    doc = await repo.get_by_id(document_id)
    if not doc or doc.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")

    storage = StorageRepository()
    await storage.delete(doc.storage_path)
    await repo.delete(doc)
