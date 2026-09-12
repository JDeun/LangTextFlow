from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "LangTextFlow"
    environment: str = "development"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    cors_origin_regex: str = (
        r"^https?://(localhost|127\.0\.0\.1|"
        r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
        r"192\.168\.\d{1,3}\.\d{1,3}|"
        r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
        r"100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}|"
        r"[A-Za-z0-9-]+\.local)(:\d+)?$"
    )
    frontend_port: int = 5173
    backend_port: int = 8000
    max_segments: int = 100
    database_path: str = "data/langtextflow.db"
    vibevoice_url: str = "http://127.0.0.1:8001"
    audio_queue_chunks: int = 32
    max_audio_frame_bytes: int = 1024 * 1024
    vad_threshold_dbfs: float = -45.0
    vad_hangover_frames: int = 3
    audio_backpressure_warn_ms: float = 50.0
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
