"""OAuth 2.0 Google + client HTTP authentifié (Agenda, Drive)."""

import time
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.services.settings import get_secret_json, load_settings, set_kv, set_secret_json

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events.readonly",
    "https://www.googleapis.com/auth/drive",
]
TOKENS_KEY = "google_tokens"
ACCOUNT_KEY = "google_account"


class GoogleNotConnected(Exception):
    pass


def build_auth_url(client_id: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": get_config().oauth_redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _with_expiry(tokens: dict) -> dict:
    tokens["expires_at"] = time.time() + int(tokens.get("expires_in", 3600))
    return tokens


async def exchange_code(db: AsyncSession, code: str, http: httpx.AsyncClient | None = None) -> str:
    """Échange le code d'autorisation, stocke les jetons, renvoie l'e-mail du compte."""
    settings = await load_settings(db)
    async with _client(http) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google.client_id,
                "client_secret": settings.google.client_secret,
                "redirect_uri": get_config().oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        tokens = _with_expiry(resp.json())
        info = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"})
        email = info.json().get("email", "") if info.status_code == 200 else ""
    await set_secret_json(db, TOKENS_KEY, tokens)
    await set_kv(db, ACCOUNT_KEY, email)
    return email


async def get_access_token(db: AsyncSession, http: httpx.AsyncClient | None = None) -> str:
    tokens = await get_secret_json(db, TOKENS_KEY)
    if not tokens:
        raise GoogleNotConnected("Compte Google non connecté")
    if tokens.get("expires_at", 0) > time.time() + 60:
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        raise GoogleNotConnected("Jeton de rafraîchissement absent : reconnectez le compte Google")
    settings = await load_settings(db)
    async with _client(http) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.google.client_id,
                "client_secret": settings.google.client_secret,
                "refresh_token": tokens["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
    if resp.status_code >= 400:
        raise GoogleNotConnected(f"Rafraîchissement du jeton refusé ({resp.status_code}) : reconnectez le compte")
    tokens.update(_with_expiry(resp.json()))
    await set_secret_json(db, TOKENS_KEY, tokens)
    return tokens["access_token"]


async def disconnect(db: AsyncSession) -> None:
    await set_secret_json(db, TOKENS_KEY, None)
    await set_kv(db, ACCOUNT_KEY, "")


class _client:
    """Réutilise le client fourni (tests) ou en crée un le temps de l'appel."""

    def __init__(self, http: httpx.AsyncClient | None):
        self._given = http
        self._own: httpx.AsyncClient | None = None

    async def __aenter__(self) -> httpx.AsyncClient:
        if self._given:
            return self._given
        self._own = httpx.AsyncClient(timeout=30)
        return self._own

    async def __aexit__(self, *exc):
        if self._own:
            await self._own.aclose()


async def google_client(db: AsyncSession, timeout: float = 60) -> httpx.AsyncClient:
    """Client httpx portant le jeton d'accès (à fermer par l'appelant)."""
    token = await get_access_token(db)
    return httpx.AsyncClient(timeout=timeout, headers={"Authorization": f"Bearer {token}"})
