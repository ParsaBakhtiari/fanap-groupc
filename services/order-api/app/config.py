from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service_name: str = "order-service"
    environment: str = "production"
    database_url: str = "mysql+pymysql://ecommerce:ecommerce@mysql:3306/ecommerce"
    backend_url: str = "http://backend-api:8000"
    jwt_secret: str = Field(min_length=32)
    jwt_issuer: str = "ecommerce-backend"
    jwt_audience: str = "ecommerce"
    internal_service_token: str = Field(min_length=32)
    auto_create_schema: bool = False

    object_storage_endpoint: str | None = None
    object_storage_region: str = "us-east-1"
    object_storage_bucket: str = "ecommerce"
    object_storage_access_key: str | None = None
    object_storage_secret_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
