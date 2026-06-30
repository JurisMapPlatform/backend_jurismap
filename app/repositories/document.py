import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    def __init__(self, db: AsyncSession):
        super().__init__(Document, db)

    async def get_by_user(self, user_id: uuid.UUID) -> list[Document]:
        result = await self.db.execute(
            select(Document).where(Document.user_id == user_id).order_by(Document.created_at.desc())
        )
        return result.scalars().all()

    async def get_by_ids(self, ids: list[uuid.UUID], user_id: uuid.UUID) -> list[Document]:
        result = await self.db.execute(
            select(Document).where(Document.id.in_(ids), Document.user_id == user_id)
        )
        return result.scalars().all()
