from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    mock_x: bool
    x_access_token: str
    cache_path: Path
    request_timeout_seconds: float = 15.0
    voice_model: str = "base.en"
    voice_model_path: Path = PROJECT_ROOT / ".models" / "faster-whisper" / "base.en"
    voice_record_seconds: float = 8.0
    voice_sample_rate: int = 16_000
    voice_input_device: int | str | None = None
    voice_language: str = "en"
    voice_compute_device: str = "cpu"
    voice_compute_type: str = "int8"
    voice_cpu_threads: int = 0
    voice_num_workers: int = 1
    device_fullscreen: bool = False
    device_fps: int = 30
    mock_x_base_url: str = ""
    mock_x_host: str = "127.0.0.1"
    mock_x_port: int = 8765
    mock_x_database_path: Path = PROJECT_ROOT / ".cache" / "mock_x_platform.sqlite3"


def load_settings(env_file: str | Path | None = None) -> Settings:
    load_dotenv(dotenv_path=env_file, override=False)
    cache_value = os.getenv("CACHE_PATH", ".cache/x_user_search.json")
    cache_path = Path(cache_value)
    if not cache_path.is_absolute():
        cache_path = PROJECT_ROOT / cache_path

    voice_model = os.getenv("VOICE_MODEL", "base.en").strip() or "base.en"
    model_value = os.getenv(
        "VOICE_MODEL_PATH", f".models/faster-whisper/{voice_model}"
    )
    voice_model_path = Path(model_value)
    if not voice_model_path.is_absolute():
        voice_model_path = PROJECT_ROOT / voice_model_path

    device_value = os.getenv("VOICE_INPUT_DEVICE", "").strip()
    voice_input_device: int | str | None
    if not device_value:
        voice_input_device = None
    elif device_value.isdecimal():
        voice_input_device = int(device_value)
    else:
        voice_input_device = device_value

    record_seconds = float(os.getenv("VOICE_RECORD_SECONDS", "8"))
    if not 1 <= record_seconds <= 30:
        raise ValueError("VOICE_RECORD_SECONDS must be between 1 and 30.")

    sample_rate = int(os.getenv("VOICE_SAMPLE_RATE", "16000"))
    if not 8_000 <= sample_rate <= 48_000:
        raise ValueError("VOICE_SAMPLE_RATE must be between 8000 and 48000.")

    cpu_threads = int(os.getenv("VOICE_CPU_THREADS", "0"))
    if not 0 <= cpu_threads <= 64:
        raise ValueError("VOICE_CPU_THREADS must be between 0 and 64.")

    num_workers = int(os.getenv("VOICE_NUM_WORKERS", "1"))
    if not 1 <= num_workers <= 4:
        raise ValueError("VOICE_NUM_WORKERS must be between 1 and 4.")

    device_fps = int(os.getenv("DEVICE_FPS", "30"))
    if not 15 <= device_fps <= 60:
        raise ValueError("DEVICE_FPS must be between 15 and 60.")

    mock_x_port = int(os.getenv("MOCK_X_PORT", "8765"))
    if not 1 <= mock_x_port <= 65_535:
        raise ValueError("MOCK_X_PORT must be between 1 and 65535.")

    mock_database_value = os.getenv(
        "MOCK_X_DATABASE_PATH", ".cache/mock_x_platform.sqlite3"
    )
    mock_x_database_path = Path(mock_database_value)
    if not mock_x_database_path.is_absolute():
        mock_x_database_path = PROJECT_ROOT / mock_x_database_path

    return Settings(
        mock_x=_as_bool(os.getenv("MOCK_X"), default=True),
        x_access_token=os.getenv("X_ACCESS_TOKEN", "").strip(),
        cache_path=cache_path,
        request_timeout_seconds=float(os.getenv("X_REQUEST_TIMEOUT", "15")),
        voice_model=voice_model,
        voice_model_path=voice_model_path,
        voice_record_seconds=record_seconds,
        voice_sample_rate=sample_rate,
        voice_input_device=voice_input_device,
        voice_language=os.getenv("VOICE_LANGUAGE", "en").strip() or "en",
        voice_compute_device=os.getenv("VOICE_COMPUTE_DEVICE", "cpu").strip() or "cpu",
        voice_compute_type=os.getenv("VOICE_COMPUTE_TYPE", "int8").strip() or "int8",
        voice_cpu_threads=cpu_threads,
        voice_num_workers=num_workers,
        device_fullscreen=_as_bool(os.getenv("DEVICE_FULLSCREEN"), default=False),
        device_fps=device_fps,
        mock_x_base_url=os.getenv("MOCK_X_BASE_URL", "").strip().rstrip("/"),
        mock_x_host=os.getenv("MOCK_X_HOST", "127.0.0.1").strip() or "127.0.0.1",
        mock_x_port=mock_x_port,
        mock_x_database_path=mock_x_database_path,
    )
