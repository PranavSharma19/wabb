from __future__ import annotations

import importlib
import math
import os
import time
import wave
from pathlib import Path
from types import ModuleType
from typing import Callable

from voice.errors import VoiceInputError


def record_microphone(
    output_path: str | Path,
    *,
    duration_seconds: float = 8.0,
    sample_rate: int = 16_000,
    input_device: int | str | None = None,
    on_progress: Callable[[int], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    _sounddevice: ModuleType | None = None,
    _clock: Callable[[], float] = time.monotonic,
    _sleep: Callable[[float], None] = time.sleep,
    _enter_pressed: Callable[[], bool] | None = None,
) -> Path:
    """Record mono PCM until Enter is pressed or the duration cap is reached."""

    if not 1 <= duration_seconds <= 30:
        raise ValueError("Recording duration must be between 1 and 30 seconds.")
    if not 8_000 <= sample_rate <= 48_000:
        raise ValueError("Sample rate must be between 8000 and 48000 Hz.")

    sounddevice = _sounddevice or _load_sounddevice()
    chunks: list[bytes] = []
    stream_problems: list[str] = []

    def audio_callback(indata: object, _frames: int, _time_info: object, status: object) -> None:
        if status:
            stream_problems.append(str(status))
        chunks.append(bytes(indata))

    should_stop = stop_requested or _enter_pressed or _windows_enter_pressed
    try:
        sounddevice.check_input_settings(
            device=input_device,
            channels=1,
            dtype="int16",
            samplerate=sample_rate,
        )
        with sounddevice.RawInputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="int16",
            device=input_device,
            blocksize=0,
            callback=audio_callback,
        ):
            started_at = _clock()
            last_remaining: int | None = None
            while True:
                elapsed = _clock() - started_at
                remaining = max(0, math.ceil(duration_seconds - elapsed))
                if on_progress is not None and remaining != last_remaining:
                    on_progress(remaining)
                    last_remaining = remaining
                if elapsed >= duration_seconds or should_stop():
                    break
                _sleep(min(0.05, duration_seconds - elapsed))
    except Exception as exc:
        device_hint = f" device {input_device!r}" if input_device is not None else " the default input device"
        raise VoiceInputError(f"Could not record from{device_hint}: {exc}") from exc

    if stream_problems:
        raise VoiceInputError(
            "The audio input stream reported a problem: " + "; ".join(stream_problems)
        )
    if not chunks:
        raise VoiceInputError("No microphone audio was captured.")

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"".join(chunks))
    except OSError as exc:
        raise VoiceInputError(f"Could not save the temporary recording: {exc}") from exc
    return path


def _windows_enter_pressed() -> bool:
    """Consume an Enter key without echoing; early stop is Windows-first for this CLI."""

    if os.name != "nt":
        return False
    import msvcrt

    while msvcrt.kbhit():
        key = msvcrt.getwch()
        if key in {"\r", "\n"}:
            return True
        # Consume the second byte emitted by function/arrow keys.
        if key in {"\x00", "\xe0"} and msvcrt.kbhit():
            msvcrt.getwch()
    return False


def available_input_devices(*, _sounddevice: ModuleType | None = None) -> list[tuple[int, str]]:
    sounddevice = _sounddevice or _load_sounddevice()
    try:
        devices = sounddevice.query_devices()
    except Exception as exc:
        raise VoiceInputError(f"Could not inspect audio devices: {exc}") from exc
    return [
        (index, str(device["name"]))
        for index, device in enumerate(devices)
        if int(device.get("max_input_channels", 0)) > 0
    ]


def _load_sounddevice() -> ModuleType:
    try:
        return importlib.import_module("sounddevice")
    except ImportError as exc:
        raise VoiceInputError(
            "Offline voice dependencies are not installed. Run: "
            "python -m pip install -r requirements-voice.txt"
        ) from exc
