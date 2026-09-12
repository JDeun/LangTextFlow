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
    audience_join_max_failures: int = 8
    audience_join_window_seconds: float = 60.0
    audience_join_block_seconds: float = 300.0
    audience_join_max_tracked_clients: int = 4096
    vibevoice_url: str = "http://127.0.0.1:8001"
    vibevoice_repo_path: str = ""
    vibevoice_python: str = ""
    vibevoice_model_path: str = ""
    vibevoice_tensor_parallel_size: int = 1
    vibevoice_max_model_len: int = 16384
    vibevoice_max_audio_windows: int = 512
    vibevoice_mm_processor_cache_gb: float = 16.0
    vibevoice_gpu_memory_utilization: float = 0.85
    vibevoice_startup_timeout_seconds: float = 600.0
    audio_queue_chunks: int = 32
    max_audio_frame_bytes: int = 1024 * 1024
    asr_replay_seconds: float = 8.0
    asr_health_check_seconds: float = 0.25
    faster_whisper_model: str = "small"
    faster_whisper_device: str = "auto"
    faster_whisper_compute_type: str = "default"
    faster_whisper_chunk_seconds: float = 4.0
    vad_threshold_dbfs: float = -45.0
    vad_hangover_frames: int = 3
    audio_backpressure_warn_ms: float = 50.0
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_correction_model: str = "qwen3.5:4b"
    correction_timeout_seconds: float = 4.0
    ollama_translation_model: str = "translategemma:4b"

    model_config = SettingsConfigDict(
        env_prefix="LANGTEXTFLOW_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
