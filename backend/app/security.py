from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException, Request, WebSocket
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.db import get_db
from app.models import User

COOKIE = "pdn_session"
MAX_AGE = 7 * 24 * 3600
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, Exception):
        return False


def _serializer(salt: str = "session") -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_config().app_secret_key, salt=salt)


def make_token(value, salt: str = "session") -> str:
    return _serializer(salt).dumps(value)


def read_token(token: str, salt: str = "session", max_age: int = MAX_AGE):
    try:
        return _serializer(salt).loads(token, max_age=max_age)
    except BadSignature:
        return None


async def current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    user_id = read_token(request.cookies.get(COOKIE, ""))
    user = await db.get(User, user_id) if user_id else None
    if not user:
        raise HTTPException(401, "Non authentifié")
    return user


def websocket_user_id(ws: WebSocket) -> int | None:
    return read_token(ws.cookies.get(COOKIE, ""))
