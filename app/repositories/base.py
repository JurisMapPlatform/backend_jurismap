import uuid
from typing import TypeVar, Generic, Type

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base

T = TypeVar("T", bound=Base)


class BaseRepository(Generic[T]):
    def __init__(self, model: Type[T], db: AsyncSession):
        self.model = model
        self.db = db

    async def get_by_id(self, id: uuid.UUID) -> T | None:
        return await self.db.get(self.model, id)

    async def create(self, entity: T) -> T:
        self.db.add(entity)
        await self.db.commit()
        await self.db.refresh(entity)
        return entity

    async def update(self, entity: T) -> T:
        await self.db.commit()
        await self.db.refresh(entity)
        return entity

    async def delete(self, entity: T) -> None:
        await self.db.delete(entity)
        await self.db.commit()

    async def count(self, *filters) -> int:
        query = select(func.count()).select_from(self.model).where(*filters)
        result = await self.db.execute(query)
        return result.scalar_one()
