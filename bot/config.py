import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
MAX_CONCURRENT_MEETINGS = int(os.environ.get("MAX_CONCURRENT_MEETINGS", "3"))
SCREEN = (1280, 720)

BOT_COMMANDS = "bot:commands"
BOT_EVENTS = "bot:events"
STATE_FILE = DATA_DIR / "browser" / "state.enc"
PROFILE_DIR = DATA_DIR / "browser" / "profile"


def control_key(meeting_id: int) -> str:
    return f"bot:control:{meeting_id}"


def meeting_dir(meeting_id: int) -> Path:
    return DATA_DIR / "meetings" / str(meeting_id)


def fernet() -> Fernet:
    """Même dérivation de clé que le backend (APP_SECRET_KEY)."""
    digest = hashlib.sha256(os.environ.get("APP_SECRET_KEY", "change-me").encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))
