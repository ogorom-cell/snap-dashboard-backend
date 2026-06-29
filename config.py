from __future__ import annotations
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    SNAP_CLIENT_ID: str
    SNAP_CLIENT_SECRET: str
    REDIRECT_URI: str
    DATABASE_URL: str
    JWT_SECRET: str
    JWT_EXPIRE_HOURS: int = 24
    FRONTEND_URL: str = "http://localhost:3000"

    model_config = {"env_file": ".env"}


settings = Settings()
