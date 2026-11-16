from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_config

_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def utcnow() -> datetime:
    """Toutes les dates sont stockées en UTC « naïf »."""
    return datetime.now(UTC).replace(tzinfo=None)


def init_engine(url: str | None = None):
    global _engine, _sessionmaker
    _engine = create_async_engine(url or get_config().database_url, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session
