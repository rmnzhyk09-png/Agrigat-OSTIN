"""Управление базой данных."""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from .models import Base
from ..config import settings

# Директория для SQLite-файла должна существовать до создания движка
if settings.db_url.startswith("sqlite"):
    _db_file = settings.db_url.split("///")[-1]
    if _db_file and _db_file != ":memory:":
        Path(_db_file).parent.mkdir(parents=True, exist_ok=True)


# Синхронный движок
sync_engine = create_engine(settings.db_url.replace("+aiosqlite", ""))
SyncSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)


# Асинхронный движок (для aiogram)
async_engine = create_async_engine(settings.db_url, echo=False)
AsyncSessionLocal = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    """Создание таблиц."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ База данных инициализирована")


async def get_session() -> AsyncSession:
    """Получить сессию."""
    async with AsyncSessionLocal() as session:
        yield session
