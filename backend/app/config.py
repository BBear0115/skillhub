import os
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _resolve_storage_root(path_value: str, database_url: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        path = (BACKEND_DIR / path).resolve()
    # In-memory SQLite resets IDs on every process start, so give each process
    # an isolated storage root to avoid stale skill directories colliding.
    if database_url == "sqlite://":
        path = path / f"runtime-{os.getpid()}"
    return str(path)


def _resolve_sqlite_url(database_url: str) -> str:
    if database_url == "sqlite://":
        return database_url
    if not database_url.startswith("sqlite:///"):
        return database_url

    raw_path = database_url[len("sqlite:///") :]
    path = Path(raw_path)
    if not path.is_absolute():
        path = (BACKEND_DIR / path).resolve()
    return f"sqlite:///{path.as_posix()}"


class Settings(BaseSettings):
    database_url: str = "sqlite:///./data/skillhub.db"
    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 60 * 24  # 1 day
    algorithm: str = "HS256"
    frontend_url: str = "http://localhost:5173"
    storage_root: str = "./storage"
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    @model_validator(mode="after")
    def normalize_paths(self) -> "Settings":
        self.database_url = _resolve_sqlite_url(self.database_url)
        self.storage_root = _resolve_storage_root(self.storage_root, self.database_url)
        return self


settings = Settings()
