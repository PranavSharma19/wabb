from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

from config import Settings
from voice.errors import VoiceInputCancelled
from voice.recorder import record_microphone
from voice.transcriber import OfflineWhisperTranscriber, SpeechTranscriber


Recorder = Callable[..., Path]
StatusCallback = Callable[[str], None]


class OfflineVoiceInput:
    """Coordinates bounded microphone capture with a reusable local model."""

    def __init__(
        self,
        settings: Settings,
        *,
        recorder: Recorder = record_microphone,
        transcriber: SpeechTranscriber | None = None,
    ) -> None:
        self.settings = settings
        self.recorder = recorder
        self.transcriber = transcriber or OfflineWhisperTranscriber(settings)

    def listen(
        self,
        *,
        on_status: StatusCallback | None = None,
        on_progress: Callable[[int], None] | None = None,
        stop_requested: Callable[[], bool] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> str:
        with TemporaryDirectory(prefix="x-user-finder-voice-") as temporary_dir:
            audio_path = Path(temporary_dir) / "recording.wav"
            self.recorder(
                audio_path,
                duration_seconds=self.settings.voice_record_seconds,
                sample_rate=self.settings.voice_sample_rate,
                input_device=self.settings.voice_input_device,
                on_progress=on_progress,
                stop_requested=stop_requested,
            )
            if on_status is not None:
                on_status("Recording complete. Transcribing locally...")
            if cancel_requested is not None and cancel_requested():
                raise VoiceInputCancelled("Voice capture was cancelled.")
            transcript = self.transcriber.transcribe_audio(audio_path)
            if cancel_requested is not None and cancel_requested():
                raise VoiceInputCancelled("Voice transcription was cancelled.")
            return transcript
