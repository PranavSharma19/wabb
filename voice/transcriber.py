from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Callable, Protocol

from config import Settings, load_settings
from voice.errors import VoiceInputError


class SpeechTranscriber(Protocol):
    def transcribe_audio(self, audio_path: str | Path) -> str: ...


class OfflineWhisperTranscriber:
    """Lazy, reusable faster-whisper adapter that only loads local model files."""

    def __init__(
        self,
        settings: Settings,
        *,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self._model_factory = model_factory
        self._model: Any | None = None

    def transcribe_audio(self, audio_path: str | Path) -> str:
        path = Path(audio_path)
        if not path.is_file():
            raise VoiceInputError(f"Audio file does not exist: {path}")

        model = self._get_model()
        try:
            segments, _info = model.transcribe(
                str(path),
                language=self.settings.voice_language,
                beam_size=5,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                condition_on_previous_text=False,
            )
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        except Exception as exc:
            raise VoiceInputError(f"Offline transcription failed: {exc}") from exc
        if not text:
            raise VoiceInputError("No speech was detected. Try again closer to the microphone.")
        return text

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model

        model_path = self.settings.voice_model_path
        if not (model_path / "model.bin").is_file():
            raise VoiceInputError(
                f"Offline Whisper model is not installed at {model_path}. "
                "While online once, run: python -m voice.download_model"
            )
        factory = self._model_factory or _load_whisper_model()
        try:
            self._model = factory(
                str(model_path),
                device=self.settings.voice_compute_device,
                compute_type=self.settings.voice_compute_type,
                cpu_threads=self.settings.voice_cpu_threads,
                num_workers=self.settings.voice_num_workers,
                local_files_only=True,
            )
        except Exception as exc:
            raise VoiceInputError(f"Could not load the offline Whisper model: {exc}") from exc
        return self._model


def transcribe_audio(
    audio_path: str | Path,
    *,
    settings: Settings | None = None,
    transcriber: SpeechTranscriber | None = None,
) -> str:
    """Simple future-facing speech-to-text entry point."""

    active_settings = settings or load_settings()
    active_transcriber = transcriber or OfflineWhisperTranscriber(active_settings)
    return active_transcriber.transcribe_audio(audio_path)


def _load_whisper_model() -> Callable[..., Any]:
    try:
        module = importlib.import_module("faster_whisper")
    except ImportError as exc:
        raise VoiceInputError(
            "Offline voice dependencies are not installed. Run: "
            "python -m pip install -r requirements-voice.txt"
        ) from exc
    return module.WhisperModel
