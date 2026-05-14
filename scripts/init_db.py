"""
Initialize the axiom_flow database tables.
Usage: python scripts/init_db.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Import models FIRST to populate Base.metadata
from app.models import Document, LayoutBlock  # noqa
from app.core.database import engine, Base
from app.core.config import Settings


async def init_db():
    settings = Settings()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        print("Tables created successfully")

    tables = list(Base.metadata.tables.keys())
    print(f"Tables: {tables}")
    await engine.dispose()


if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
