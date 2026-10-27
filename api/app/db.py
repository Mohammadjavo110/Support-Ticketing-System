"""Async SQLAlchemy engine, session factory and one-shot schema bootstrap."""

import logging
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

log = logging.getLogger("app.db")

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Apply schema.sql once per process.

    The script is executed through the asyncpg driver connection in one call
    (autocommit style, simple query protocol). Every statement is
    ``if not exists``, so re-running on restart is a no-op.
    """
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        await raw.driver_connection.execute(script)
    log.info("schema applied")
