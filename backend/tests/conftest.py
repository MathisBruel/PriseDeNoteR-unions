import os

os.environ.setdefault("APP_SECRET_KEY", "test-secret")
os.environ.setdefault("DATA_DIR", "/tmp/pdn-tests")

import fakeredis.aioredis
import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import bus, db
from app.main import app
from app.models import Base


@pytest.fixture(autouse=True)
async def env(monkeypatch):
    """Base SQLite en mémoire, Redis simulé et file de tâches interceptée."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db._engine = engine
    db._sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    bus.set_redis(redis)
    jobs: list[tuple] = []

    async def fake_enqueue(job, *args):
        jobs.append((job, *args))

    monkeypatch.setattr(bus, "enqueue", fake_enqueue)
    yield {"redis": redis, "jobs": jobs}
    await redis.aclose()
    await engine.dispose()


@pytest.fixture
async def session():
    async with db.get_sessionmaker()() as s:
        yield s


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin(client):
    resp = await client.post("/api/auth/setup", json={"email": "admin@example.com", "password": "motdepasse123"})
    assert resp.status_code == 200
    return client
