from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.db import get_db
from app.models import User
from app.security import current_user, make_token, read_token
from app.services import bot_control, drive_export, google
from app.services.settings import get_kv, load_settings, log_event
from app.tasks.worker import sync_calendar_job

router = APIRouter(prefix="/api", tags=["google"])


@router.get("/google/status")
async def status(db: AsyncSession = Depends(get_db), _: User = Depends(current_user)):
    settings = await load_settings(db)
    return {
        "configured": bool(settings.google.client_id and settings.google.client_secret),
        "connected": bool(await google.get_secret_json(db, google.TOKENS_KEY)),
        "account": await get_kv(db, google.ACCOUNT_KEY, ""),
        "redirect_uri": get_config().oauth_redirect_uri,
        "browser_session": await get_kv(db, bot_control.BROWSER_SESSION_KEY, None),
    }


@router.get("/google/oauth/start")
async def oauth_start(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    settings = await load_settings(db)
    if not settings.google.client_id or not settings.google.client_secret:
        raise HTTPException(400, "Renseignez d'abord l'identifiant et le secret client OAuth")
    return {"url": google.build_auth_url(settings.google.client_id, make_token(user.id, salt="oauth"))}


@router.get("/google/oauth/callback")
async def oauth_callback(code: str = "", state: str = "", error: str = "", db: AsyncSession = Depends(get_db)):
    target = f"{get_config().public_url.rstrip('/')}/settings"
    if error or not code or read_token(state, salt="oauth", max_age=900) is None:
        return RedirectResponse(f"{target}?google=error")
    try:
        email = await google.exchange_code(db, code)
    except Exception as exc:
        await log_event(db, f"Connexion Google en échec : {exc}", "error")
        return RedirectResponse(f"{target}?google=error")
    await log_event(db, f"Compte Google connecté : {email}")
    return RedirectResponse(f"{target}?google=ok")


@router.post("/google/disconnect")
async def disconnect(db: AsyncSession = Depends(get_db), _: User = Depends(current_user)):
    await google.disconnect(db)
    return {"ok": True}


@router.post("/google/sync")
async def sync_now(_: User = Depends(current_user)):
    result = await sync_calendar_job()
    if result is None:
        raise HTTPException(400, "Synchronisation impossible : vérifiez la connexion Google et les journaux")
    return result


@router.post("/google/browser/{action}")
async def browser_login(action: str, _: User = Depends(current_user)):
    """Connexion manuelle du navigateur du bot via noVNC : start | finish | cancel."""
    if action not in ("start", "finish", "cancel"):
        raise HTTPException(404)
    await bot_control.send_manager_command(f"login_{action}")
    return {"ok": True}


@router.get("/drive/drives")
async def drives(db: AsyncSession = Depends(get_db), _: User = Depends(current_user)):
    client = await _client(db)
    try:
        return await drive_export.list_drives(client)
    finally:
        await client.aclose()


@router.get("/drive/folders")
async def folders(
    parent_id: str, drive_id: str = "", db: AsyncSession = Depends(get_db), _: User = Depends(current_user)
):
    client = await _client(db)
    try:
        return await drive_export.list_folders(client, parent_id, drive_id)
    finally:
        await client.aclose()


async def _client(db: AsyncSession):
    try:
        return await google.google_client(db)
    except google.GoogleNotConnected as exc:
        raise HTTPException(400, str(exc)) from exc
