"""Application settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/mercadodirecto"
    # Set DATABASE_URL=sqlite:///./mercadodirecto.db for zero-dependency local dev.
    jwt_secret: str = "change-me-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    cors_origins: str = "http://localhost:3000,http://localhost:3001"

    # VelaFi payments (https://docs.velafi.com)
    velafi_base_url: str = "https://api-test.velafi.com"  # sandbox by default
    velafi_api_key: str = ""  # X-BH-TOKEN
    velafi_webhook_public_key: str = ""  # RSA public key for webhook verification

    # Mono payments (https://docs.mono.la) — fiat payin (PSE) + farmer payouts (Transfers)
    mono_base_url: str = "https://api.mono.co"  # Mono API base (confirm sandbox URL during live integration)
    mono_api_key: str = ""  # X-API-KEY
    mono_webhook_secret: str = ""  # HMAC secret for webhook verification

    app_base_url: str = "http://localhost:8000"  # used to build webhook callback URL


settings = Settings()
