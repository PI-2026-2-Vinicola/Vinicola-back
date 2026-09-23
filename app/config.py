from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configurações lidas de variáveis de ambiente (ou do arquivo .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./osais.db"
    jwt_secret: str = "osais-dev-secret-troque-em-producao-0123456789"
    jwt_expires_minutes: int = 720
    osais_detector: str = "mock"
    osais_model_path: str = "models/osais-grapes.pt"
    osais_model_confidence: float = 0.35
    storage_dir: str = "storage"
    seed_demo: bool = True
    public_read: bool = True
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
