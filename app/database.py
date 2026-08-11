from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# Pool acotado: Cloud SQL db-f1-micro admite ~25 conexiones en total. Con máx. 4 por instancia
# (2 base + 2 overflow) y maxScale=5 en Cloud Run, el peor caso son 20 conexiones, bajo el límite.
# pool_pre_ping evita errores por conexiones que Cloud SQL cerró por inactividad.
engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=2,
    max_overflow=2,
    pool_timeout=30,
    pool_recycle=1800,
    pool_pre_ping=True,
)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session
