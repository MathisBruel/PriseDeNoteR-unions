import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt, encrypt
from app.models import EventLog, Setting
from app.schemas import MASK, SECRET_FIELDS, AppSettings

APP_KEY = "app"


async def get_kv(db: AsyncSession, key: str, default=None):
    row = await db.get(Setting, key)
    return row.value if row else default


async def set_kv(db: AsyncSession, key: str, value) -> None:
    row = await db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    await db.commit()


async def get_secret_json(db: AsyncSession, key: str):
    raw = decrypt(await get_kv(db, key, "") or "")
    return json.loads(raw) if raw else None


async def set_secret_json(db: AsyncSession, key: str, value) -> None:
    await set_kv(db, key, encrypt(json.dumps(value)) if value is not None else "")


async def load_settings(db: AsyncSession) -> AppSettings:
    """Paramètres avec secrets déchiffrés (usage interne uniquement)."""
    data = await get_kv(db, APP_KEY, {}) or {}
    settings = AppSettings.model_validate(data)
    for section, field in SECRET_FIELDS:
        obj = getattr(settings, section)
        setattr(obj, field, decrypt(getattr(obj, field)))
    return settings


async def save_settings(db: AsyncSession, incoming: AppSettings) -> AppSettings:
    """Enregistre ; un secret vide ou masqué conserve la valeur existante."""
    current = await load_settings(db)
    for section, field in SECRET_FIELDS:
        obj = getattr(incoming, section)
        if getattr(obj, field) in ("", MASK):
            setattr(obj, field, getattr(getattr(current, section), field))
    stored = incoming.model_dump()
    for section, field in SECRET_FIELDS:
        stored[section][field] = encrypt(stored[section][field])
    await set_kv(db, APP_KEY, stored)
    return incoming


def masked(settings: AppSettings) -> dict:
    data = settings.model_dump()
    for section, field in SECRET_FIELDS:
        data[section][field] = MASK if data[section][field] else ""
    return data


async def log_event(db: AsyncSession, message: str, level: str = "info", meeting_id: int | None = None):
    db.add(EventLog(meeting_id=meeting_id, level=level, message=message[:4000]))
    await db.commit()
