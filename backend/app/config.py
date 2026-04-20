from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./skillhub.db"
    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_minutes: int = 60 * 24  # 1 day
    algorithm: str = "HS256"
    frontend_url: str = "http://localhost:5173"
    storage_root: str = "./storage"

    class Config:
        env_file = ".env"


settings = Settings()
