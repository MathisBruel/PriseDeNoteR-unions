from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Configuration d'infrastructure (variables d'environnement)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://pdn:pdn@postgres:5432/pdn"
    redis_url: str = "redis://redis:6379/0"
    app_secret_key: str = "change-me"
    public_url: str = "http://localhost:8080"
    data_dir: Path = Path("/data")
    cookie_secure: bool = False
    timezone: str = "Europe/Paris"  # fuseau utilisé dans les documents générés

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_url.rstrip('/')}/api/google/oauth/callback"

    def meeting_dir(self, meeting_id: int) -> Path:
        return self.data_dir / "meetings" / str(meeting_id)


@lru_cache
def get_config() -> Config:
    return Config()
