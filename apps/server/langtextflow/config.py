from functools import lru_cache

from pydantic import Field, field_validator
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
    frontend_port: int = Field(default=5173, ge=1, le=65535)
    backend_port: int = Field(default=8000, ge=1, le=65535)
    max_segments: int = Field(default=100, ge=1, le=10_000)
    database_path: str = "data/langtextflow.db"
    mdns_enabled: bool = True
    mdns_hostname: str = ""
    mdns_service_name: str = "LangTextFlow"
    audience_join_max_failures: int = Field(default=8, ge=1, le=10_000)
    audience_join_window_seconds: float = Field(default=60.0, gt=0, le=86_400)
    audience_join_block_seconds: float = Field(default=300.0, gt=0, le=86_400)
    audience_join_max_tracked_clients: int = Field(default=4096, ge=1, le=1_000_000)
    vibevoice_url: str = "http://127.0.0.1:8001"
    vibevoice_repo_path: str = ""
    vibevoice_python: str = ""
    vibevoice_model_path: str = ""
    vibevoice_tensor_parallel_size: int = Field(default=1, ge=1, le=64)
    vibevoice_max_model_len: int = Field(default=16384, ge=1)
    vibevoice_max_audio_windows: int = Field(default=512, ge=1)
    vibevoice_mm_processor_cache_gb: float = Field(default=16.0, ge=0)
    vibevoice_gpu_memory_utilization: float = Field(default=0.85, gt=0, le=1)
    vibevoice_startup_timeout_seconds: float = Field(default=600.0, gt=0)
    audio_queue_chunks: int = Field(default=32, ge=1, le=4096)
    max_audio_frame_bytes: int = Field(default=1024 * 1024, ge=4, le=8 * 1024 * 1024)
    asr_replay_seconds: float = Field(default=8.0, ge=0, le=120)
    asr_health_check_seconds: float = Field(default=0.25, gt=0, le=60)
    faster_whisper_model: str = "small"
    faster_whisper_device: str = "auto"
    faster_whisper_compute_type: str = "default"
    faster_whisper_chunk_seconds: float = Field(default=4.0, gt=0, le=120)
    vad_threshold_dbfs: float = -45.0
    vad_hangover_frames: int = Field(default=3, ge=0, le=10_000)
    audio_backpressure_warn_ms: float = Field(default=50.0, ge=0)
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_correction_model: str = "qwen3.5:4b"
    correction_timeout_seconds: float = Field(default=4.0, gt=0)
    ollama_translation_model: str = "translategemma:4b"
    openai_compatible_url: str = "http://127.0.0.1:1234/v1"
    openai_compatible_api_key: str = ""
    openai_compatible_translation_model: str = ""
    openai_compatible_timeout_seconds: float = Field(default=20.0, gt=0)

    @field_validator("max_audio_frame_bytes")
    @classmethod
    def audio_frame_bytes_must_align_to_float32(cls, value: int) -> int:
        if value % 4:
            raise ValueError("max_audio_frame_bytes must be divisible by 4")
        return value

    model_config = SettingsConfigDict(
        env_prefix="LANGTEXTFLOW_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
