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

    # Tempo máximo da análise de uma imagem (detector + classificação).
    oasis_analysis_timeout_seconds: float = 30.0

    # ThingSpeak — leitura de canais pela API REST. Canal público: só o ID; canal privado: ID + Read API Key.
    thingspeak_api_url: str = "https://api.thingspeak.com"
    thingspeak_channel_id: str = ""
    thingspeak_read_api_key: str = ""
    thingspeak_timeout_seconds: float = 10.0
    # Cache das respostas (mín. 15 s: intervalo mínimo de atualização de um canal no plano gratuito).
    thingspeak_cache_seconds: int = 15
    # Intervalo de atualização automática sugerido ao painel (mín. 15 s).
    thingspeak_refresh_seconds: int = 60
    # Unidades informadas pelo responsável pelo canal, ex.: "field1=°C,field2=%". Vazio = sem unidade.
    thingspeak_field_units: str = ""

    # Embrapa AgroAPI (RespondeAgro v1 e Agritec v2) — OAuth2 client credentials da aplicação na loja da AgroAPI.
    embrapa_api_url: str = "https://api.cnptia.embrapa.br"
    embrapa_consumer_key: str = ""
    embrapa_consumer_secret: str = ""
    embrapa_timeout_seconds: float = 15.0
    # Cache das buscas do RespondeAgro e dos dados do Agritec (o plano gratuito do Agritec tem 1.000 requisições).
    embrapa_cache_hours: float = 24.0
    embrapa_agritec_cache_hours: float = 168.0
    # Livro da Coleção 500 Perguntas 500 Respostas consultado na aba Uvas.
    embrapa_book_id: str = "uva"
    # Consultas públicas à Embrapa por IP e por minuto.
    embrapa_rate_limit_per_minute: int = 30

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret(cls, v: str) -> str:
        # JWT_SECRET vazio no .env = segredo de desenvolvimento (a produção recusa esse valor).
        return v.strip() or DEFAULT_JWT_SECRET

    @field_validator("thingspeak_channel_id", "thingspeak_read_api_key", "embrapa_consumer_key", "embrapa_consumer_secret", "embrapa_book_id")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("thingspeak_channel_id")
    @classmethod
    def _channel_id(cls, v: str) -> str:
        if v and not v.isdigit():
            raise ValueError("THINGSPEAK_CHANNEL_ID deve conter apenas números (ID do canal no ThingSpeak).")
        return v

    @field_validator("thingspeak_cache_seconds", "thingspeak_refresh_seconds")
    @classmethod
    def _min_15s(cls, v: int) -> int:
        return max(15, v)

    @property
    def thingspeak_configured(self) -> bool:
        return bool(self.thingspeak_channel_id)

    @property
    def embrapa_configured(self) -> bool:
        return bool(self.embrapa_consumer_key and self.embrapa_consumer_secret)

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
