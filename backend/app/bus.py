"""Redis : commandes vers le bot, événements du bot, diffusion temps réel, file de tâches."""

import json

import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import RedisSettings

from app.config import get_config

BOT_COMMANDS = "bot:commands"  # liste : commandes pour le gestionnaire de bots
BOT_EVENTS = "bot:events"  # stream : événements émis par les bots
BOT_EVENTS_CURSOR = "bot:events:cursor"

_redis: aioredis.Redis | None = None
_arq = None


def control_key(meeting_id: int) -> str:
    return f"bot:control:{meeting_id}"


def live_channel(meeting_id: int) -> str:
    return f"live:{meeting_id}"


def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(get_config().redis_url, decode_responses=True)
    return _redis


def set_redis(client) -> None:
    global _redis
    _redis = client


async def publish_live(meeting_id: int, payload: dict) -> None:
    await get_redis().publish(live_channel(meeting_id), json.dumps(payload, default=str))


async def enqueue(job: str, *args) -> None:
    """Planifie une tâche de fond exécutée par le worker arq."""
    global _arq
    if _arq is None:
        _arq = await create_pool(RedisSettings.from_dsn(get_config().redis_url))
    await _arq.enqueue_job(job, *args)
