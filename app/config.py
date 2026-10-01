from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "oasis-dev-secret-troque-em-producao-0123456789"


class Settings(BaseSettings):
    """Configurações lidas de variáveis de ambiente (ou do arquivo .env)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "development" ou "production" — em produção, configurações inseguras impedem a inicialização.
    environment: str = "development"

    database_url: str = "sqlite:///./oasis.db"

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_expires_minutes: int = 480

    # Usuário administrador criado na primeira inicialização (quando não há usuários).
    oasis_admin_email: str = "admin@oasis.agr.br"
    oasis_admin_name: str = "Administrador OASIS"
    oasis_admin_password: str = ""  # vazio → senha aleatória exibida uma única vez no log

    # Detector: "auto" (YOLO se houver pesos, senão análise de cor), "color" ou "yolo".
    oasis_detector: str = "auto"
    oasis_model_path: str = "models/oasis-grapes.pt"
    oasis_model_confidence: float = 0.35

    # Fuso horário da propriedade (agrupamento de indicadores por dia local).
    oasis_timezone: str = "America/Recife"
    oasis_farm_name: str = ""

    storage_dir: str = "storage"
    max_upload_mb: int = 8
    max_import_mb: int = 10
    max_import_rows: int = 20000

    # Leitura sem login. Mantenha false fora de demonstrações públicas.
    public_read: bool = False

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # Bloqueio de login após tentativas falhas.
    login_max_attempts: int = 5
    login_lock_minutes: int = 15

    # Sensor fica "offline" sem comunicação por N × intervalo de captura.
    sensor_offline_factor: float = 3.0

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret(cls, v: str) -> str:
        # JWT_SECRET vazio no .env = segredo de desenvolvimento (a produção recusa esse valor).
        return v.strip() or DEFAULT_JWT_SECRET

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def validate_for_production(self) -> list[str]:
        problems = []
        if self.jwt_secret == DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32:
            problems.append("JWT_SECRET precisa ser definido com pelo menos 32 caracteres.")
        if self.public_read:
            problems.append("PUBLIC_READ deve ser false em produção.")
        if any(o == "*" for o in self.cors_list):
            problems.append("CORS_ORIGINS não pode ser '*'.")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
