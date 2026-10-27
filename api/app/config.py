"""Application settings loaded from the environment (see .env.example)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    DATABASE_URL: str = "postgresql+asyncpg://ticket:change_me_local@localhost:5432/tickets"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Sessions
    SECRET_KEY: str = "dev_only_change_me"
    SESSION_TTL_HOURS: int = 72
    CORS_ORIGINS: str = "http://localhost:5173"

    # Triage engine (consumed by the worker; part of the shared env contract)
    ENGINE_PROVIDER: str = "laya"
    LAYA_API_URL: str = ""
    LAYA_API_KEY: str = ""
    LAYA_MODEL: str = "laya-multilingual"
    ENGINE_MIN_CONFIDENCE: float = 0.55
    ENGINE_TIMEOUT_SECONDS: int = 8

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
