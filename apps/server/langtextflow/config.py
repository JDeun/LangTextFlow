from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "LangTextFlow"
    environment: str = "development"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    max_segments: int = 100
    vibevoice_url: str = "http://127.0.0.1:8001"
    audio_queue_chunks: int = 32
    max_audio_frame_bytes: int = 1024 * 1024
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_translation_model: str = "translategemma:4b"

    model_config = SettingsConfigDict(
        env_prefix="LANGTEXTFLOW_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
