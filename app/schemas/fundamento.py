import uuid

from pydantic import BaseModel


class FundamentoResponse(BaseModel):
    id: uuid.UUID
    fundamento_num: int
    texto: str
    label: str
    confidence: float
    is_selected: bool
    simplified_text: str | None
    page_number: int | None

    model_config = {"from_attributes": True}


class FundamentoDetail(BaseModel):
    id: uuid.UUID
    fundamento_num: int
    texto: str
    label: str
    confidence: float
    is_selected: bool
    simplified_text: str | None
    page_number: int | None
    document_id: uuid.UUID

    model_config = {"from_attributes": True}
