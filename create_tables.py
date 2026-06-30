"""Crea todas las tablas en la base de datos."""
import asyncio
from app.database import engine, Base
from app.models import User, Document, Analysis, AnalysisDocument, AnalysisFundamento


async def create():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tablas creadas exitosamente:")
    for table in Base.metadata.tables:
        print(f"  - {table}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(create())
