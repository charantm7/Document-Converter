from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path
import os


class Settings(BaseSettings):

    SUPABASE_URL: str
    SUPABASE_SERVICE_KEY: str
    SUPABASE_CONVERTED_BUCKET: str
    SUPABASE_RAW_BUCKET: str
    SUPABASE_COMPRESSED_BUCKET: str

    RABBITMQ_URL: str
    QUEUE_NAME: str

    CLIENT_SECRET: str
    CLIENT_ID: str

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[0]/".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
