from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import EventLog
from app.schemas import AppSettings
from app.security import current_user
from app.services import llm
from app.services.settings import load_settings, masked, save_settings

router = APIRouter(prefix="/api", tags=["settings"], dependencies=[Depends(current_user)])


@router.get("/settings")
async def read_settings(db: AsyncSession = Depends(get_db)):
    return masked(await load_settings(db))


@router.put("/settings")
async def write_settings(body: AppSettings, db: AsyncSession = Depends(get_db)):
    return masked(await save_settings(db, body))


@router.post("/settings/llm/test")
async def test_llm(db: AsyncSession = Depends(get_db)):
    """Vérifie le fournisseur IA enregistré avec une requête minimale."""
    settings = (await load_settings(db)).llm
    try:
        answer = await llm.complete(settings, "Tu es un assistant.", "Réponds uniquement par le mot : OK", 16)
    except llm.LLMError as exc:
        return {"ok": False, "message": str(exc)[:500]}
    return {"ok": True, "message": f"{llm.model_name(settings)} a répondu : {answer.strip()[:80]}"}


@router.get("/logs")
async def logs(limit: int = 200, db: AsyncSession = Depends(get_db)):
    rows = await db.scalars(select(EventLog).order_by(EventLog.id.desc()).limit(min(limit, 1000)))
    return [
        {"id": r.id, "meeting_id": r.meeting_id, "level": r.level, "message": r.message, "created_at": r.created_at}
        for r in rows
    ]
