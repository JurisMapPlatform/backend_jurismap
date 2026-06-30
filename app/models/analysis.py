import uuid
from datetime import datetime

from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    custom_prompt: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    processing_step: Mapped[int] = mapped_column(Integer, default=0)
    parties: Mapped[dict | None] = mapped_column(JSONB)
    background: Mapped[str | None] = mapped_column(Text)
    ruling: Mapped[str | None] = mapped_column(Text)
    mind_map_data: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user: Mapped["User"] = relationship(back_populates="analyses")
    document_links: Mapped[list["AnalysisDocument"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")
    findings: Mapped[list["AnalysisFundamento"]] = relationship(back_populates="analysis", cascade="all, delete-orphan")


class AnalysisDocument(Base):
    __tablename__ = "analysis_documents"

    analysis_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True)

    analysis: Mapped["Analysis"] = relationship(back_populates="document_links")
    document: Mapped["Document"] = relationship(back_populates="analysis_links")
