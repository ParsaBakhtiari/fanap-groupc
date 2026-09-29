from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "backend-api"
    environment: str = "production"
    database_url: str = "mysql+pymysql://ecommerce:ecommerce@mysql:3306/ecommerce"
    redis_url: str = "redis://:local-redis-password@redis:6379/0"
    jwt_secret: str = Field(min_length=32)
    jwt_issuer: str = "ecommerce-backend"
    jwt_audience: str = "ecommerce"
    access_token_minutes: int = Field(default=30, ge=5, le=1440)
    internal_service_token: str = Field(min_length=32)
    cors_origins: str = "http://localhost:8080"
    cart_ttl_seconds: int = Field(default=604800, ge=300)
    auto_create_schema: bool = False
    seed_demo_data: bool = False

    object_storage_endpoint: str | None = None
    object_storage_region: str = "us-east-1"
    object_storage_bucket: str = "ecommerce"
    object_storage_access_key: str | None = None
    object_storage_secret_key: str | None = None

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
