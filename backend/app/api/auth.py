from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.db import get_db
from app.models import User
from app.schemas import Credentials
from app.security import COOKIE, MAX_AGE, current_user, hash_password, make_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, user: User) -> None:
    response.set_cookie(
        COOKIE,
        make_token(user.id),
        max_age=MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=get_config().cookie_secure,
    )


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    count = await db.scalar(select(func.count()).select_from(User))
    return {"needs_setup": count == 0}


@router.post("/setup")
async def setup(body: Credentials, response: Response, db: AsyncSession = Depends(get_db)):
    """Création du premier (et unique) compte administrateur."""
    if await db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "L'application est déjà configurée")
    user = User(email=body.email.strip().lower(), password_hash=hash_password(body.password))
    db.add(user)
    await db.commit()
    _set_cookie(response, user)
    return {"email": user.email}


@router.post("/login")
async def login(body: Credentials, response: Response, db: AsyncSession = Depends(get_db)):
    user = await db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Identifiants incorrects")
    _set_cookie(response, user)
    return {"email": user.email}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {"email": user.email}


@router.get("/check")
async def check(user: User = Depends(current_user)):
    """Utilisé par nginx (auth_request) pour protéger l'accès noVNC."""
    return Response(status_code=204)
