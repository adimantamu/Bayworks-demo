from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://bay:bay@localhost:5433/bayworks"
    s3_endpoint: str = "http://localhost:4566"
    s3_public_endpoint: str = "http://localhost:4566"
    s3_access_key: str = "test"
    s3_secret_key: str = "test"
    s3_bucket: str = "bayworks"
    s3_region: str = "us-east-1"
    mock_dms_url: str = "http://localhost:8001"
    groq_api_key: str = ""
    openai_api_key: str = ""
    llm_provider: str = "auto"
    stt_provider: str = "auto"
    worker_id: str = "worker-1"
    retry_base_seconds: float = 1.5
    retry_max_seconds: float = 4.0


settings = Settings()
