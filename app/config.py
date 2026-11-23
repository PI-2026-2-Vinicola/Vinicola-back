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
    # Conexões mantidas com o banco (PostgreSQL/MySQL). O pooler gratuito do Supabase aceita poucas por cliente.
    db_pool_size: int = 10
    db_max_overflow: int = 20

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_expires_minutes: int = 480

    # Usuário administrador criado na primeira inicialização (quando não há usuários).
    oasis_admin_email: str = "admin@oasis.agr.br"
    oasis_admin_name: str = "Administrador OASIS"
    oasis_admin_password: str = ""  # vazio → senha aleatória exibida uma única vez no log

    # Detector: "auto" (YOLO se houver pesos, senão análise de cor), "color" ou "yolo".
    oasis_detector: str = "auto"
    # Modelo ONNX + ficha (.json) gerados por ml/export_model.py, ou pesos Ultralytics (.pt).
    oasis_model_path: str = "models/oasis-cachos.onnx"
    # Confiança mínima das detecções. Vazio = limiar recomendado na ficha do modelo (0,35 para pesos .pt).
    oasis_model_confidence: float | None = None
    # Classificador de variedades (ONNX + ficha). Vazio = sem previsão de variedade (fica a do talhão).
    oasis_variety_model_path: str = "models/oasis-variedades.onnx"

    # Fuso horário da propriedade (agrupamento de indicadores por dia local).
    oasis_timezone: str = "America/Recife"
    oasis_farm_name: str = ""

    # Onde ficam as fotos: "local" (STORAGE_DIR) ou "s3" (Supabase Storage, R2, B2, MinIO, AWS).
    storage_backend: str = "local"
    storage_dir: str = "storage"
    s3_endpoint_url: str = ""
    s3_region: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
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
    # Análises de imagem ao mesmo tempo; as demais esperam na fila. Cada análise usa até ~250 MB de memória.
    oasis_max_concurrent_analyses: int = 2

    # Cabeçalho com o IP real do cliente, definido por um proxy que o cliente não consegue forjar
    # (ex.: "true-client-ip,cf-connecting-ip" no Render). Vazio = IP da conexão (ou do proxy confiável do uvicorn).
    client_ip_header: str = ""

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

    @field_validator("database_url")
    @classmethod
    def _database_url(cls, v: str) -> str:
        # Render, Supabase e Neon entregam "postgres://" ou "postgresql://"; a imagem usa o driver psycopg (v3).
        v = v.strip()
        for prefix in ("postgres://", "postgresql://"):
            if v.startswith(prefix):
                return "postgresql+psycopg://" + v[len(prefix):]
        return v

    @field_validator("storage_backend")
    @classmethod
    def _storage_backend(cls, v: str) -> str:
        v = v.strip().lower() or "local"
        if v not in ("local", "s3"):
            raise ValueError('STORAGE_BACKEND deve ser "local" ou "s3".')
        return v

    @field_validator("s3_endpoint_url", "s3_region", "s3_bucket", "s3_access_key_id", "s3_secret_access_key", "client_ip_header")
    @classmethod
    def _strip_text(cls, v: str) -> str:
        return v.strip()

    @field_validator("jwt_secret")
    @classmethod
    def _jwt_secret(cls, v: str) -> str:
        # JWT_SECRET vazio no .env = segredo de desenvolvimento (a produção recusa esse valor).
        return v.strip() or DEFAULT_JWT_SECRET

    @field_validator("oasis_model_confidence", mode="before")
    @classmethod
    def _confidence(cls, v):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        value = float(v)
        if not 0 < value < 1:
            raise ValueError("OASIS_MODEL_CONFIDENCE deve estar entre 0 e 1 (ou vazio para usar o limiar da ficha do modelo).")
        return value

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
    def client_ip_headers(self) -> list[str]:
        return [h.strip().lower() for h in self.client_ip_header.split(",") if h.strip()]

    @property
    def s3_missing(self) -> list[str]:
        fields = {"S3_ENDPOINT_URL": self.s3_endpoint_url, "S3_REGION": self.s3_region, "S3_BUCKET": self.s3_bucket,
                  "S3_ACCESS_KEY_ID": self.s3_access_key_id, "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key}
        return [name for name, value in fields.items() if not value] if self.storage_backend == "s3" else []

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
        if self.s3_missing:
            problems.append(f"STORAGE_BACKEND=s3 exige {', '.join(self.s3_missing)}.")
        if self.oasis_admin_password and len(self.oasis_admin_password) < 12:
            problems.append("OASIS_ADMIN_PASSWORD precisa ter pelo menos 12 caracteres (vazio = senha aleatória exibida no log).")
        return problems

    def production_warnings(self) -> list[str]:
        """Pontos que não impedem a inicialização, mas quase sempre indicam um .env de desenvolvimento."""
        warnings = []
        insecure = [o for o in self.cors_list if o.startswith("http://") or "localhost" in o or "127.0.0.1" in o]
        if insecure:
            warnings.append(f"CORS_ORIGINS tem origens locais ou sem HTTPS ({', '.join(insecure)}); use o endereço HTTPS do frontend.")
        if self.database_url.startswith("sqlite"):
            warnings.append("DATABASE_URL usa SQLite; em produção use PostgreSQL (docker-compose.prod.yml).")
        return warnings


@lru_cache
def get_settings() -> Settings:
    return Settings()
