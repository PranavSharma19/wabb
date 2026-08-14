from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from config import Settings
from voice.recorder import record_microphone
from voice.service import OfflineVoiceInput
from voice.transcriber import OfflineWhisperTranscriber, transcribe_audio


class FakeAudio:
    def __init__(self, frames: int) -> None:
        self.frames = frames

    def tobytes(self) -> bytes:
        return b"\x00\x00" * self.frames


class FakeSoundDevice:
    def __init__(self) -> None:
        self.checked: dict[str, Any] = {}
        self.stream_settings: dict[str, Any] = {}

    def check_input_settings(self, **kwargs: Any) -> None:
        self.checked = kwargs

    def RawInputStream(self, **kwargs: Any):
        self.stream_settings = kwargs
        callback = kwargs["callback"]

        class FakeStream:
            def __enter__(self_nonlocal):
                callback(b"\x00\x00" * 16_000, 16_000, None, None)
                return self_nonlocal

            def __exit__(self_nonlocal, *_args: Any) -> None:
                return None

        return FakeStream()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        mock_x=True,
        x_access_token="",
        cache_path=tmp_path / "cache.json",
        voice_model_path=tmp_path / "model",
        voice_record_seconds=1,
    )


def test_recorder_writes_bounded_mono_pcm_wav(tmp_path) -> None:
    fake_device = FakeSoundDevice()
    fake_clock = FakeClock()
    output = tmp_path / "recording.wav"
    countdown: list[int] = []

    record_microphone(
        output,
        duration_seconds=1,
        sample_rate=16_000,
        input_device=2,
        on_progress=countdown.append,
        _sounddevice=fake_device,  # type: ignore[arg-type]
        _clock=fake_clock,
        _sleep=fake_clock.sleep,
        _enter_pressed=lambda: False,
    )

    assert fake_device.stream_settings["blocksize"] == 0
    assert fake_device.stream_settings["device"] == 2
    assert countdown[0] == 1
    assert countdown[-1] == 0
    with wave.open(str(output), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.getnframes() == 16_000


def test_recorder_can_stop_early_when_enter_is_pressed(tmp_path) -> None:
    fake_device = FakeSoundDevice()
    fake_clock = FakeClock()
    key_checks = iter((False, True))
    output = tmp_path / "early.wav"

    record_microphone(
        output,
        duration_seconds=8,
        _sounddevice=fake_device,  # type: ignore[arg-type]
        _clock=fake_clock,
        _sleep=fake_clock.sleep,
        _enter_pressed=lambda: next(key_checks),
    )

    assert output.is_file()
    assert fake_clock.now < 8


def test_transcriber_loads_local_model_once_and_materializes_segments(tmp_path) -> None:
    settings = make_settings(tmp_path)
    settings.voice_model_path.mkdir(parents=True)
    (settings.voice_model_path / "model.bin").write_bytes(b"fake")
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"fake wave")
    factory_calls: list[dict[str, Any]] = []

    class FakeModel:
        def transcribe(self, path: str, **kwargs: Any):
            assert path == str(audio_path)
            assert kwargs["language"] == "en"
            assert kwargs["vad_filter"] is True
            return iter([SimpleNamespace(text=" John Doe "), SimpleNamespace(text=" at XYZ ")]), None

    def factory(path: str, **kwargs: Any) -> FakeModel:
        factory_calls.append({"path": path, **kwargs})
        return FakeModel()

    transcriber = OfflineWhisperTranscriber(settings, model_factory=factory)

    assert transcriber.transcribe_audio(audio_path) == "John Doe at XYZ"
    assert transcriber.transcribe_audio(audio_path) == "John Doe at XYZ"
    assert len(factory_calls) == 1
    assert factory_calls[0]["local_files_only"] is True
    assert factory_calls[0]["compute_type"] == "int8"


def test_voice_service_cleans_temporary_recording(tmp_path) -> None:
    settings = make_settings(tmp_path)
    recorded_paths: list[Path] = []

    def fake_recorder(path: Path, **_kwargs: Any) -> Path:
        path.write_bytes(b"audio")
        recorded_paths.append(path)
        if callback := _kwargs.get("on_progress"):
            callback(1)
        return path

    class FakeTranscriber:
        def transcribe_audio(self, path: str | Path) -> str:
            assert Path(path).is_file()
            return "John Doe recruiter at XYZ"

    service = OfflineVoiceInput(
        settings,
        recorder=fake_recorder,
        transcriber=FakeTranscriber(),
    )

    statuses: list[str] = []
    progress: list[int] = []
    assert service.listen(on_status=statuses.append, on_progress=progress.append) == "John Doe recruiter at XYZ"
    assert statuses == ["Recording complete. Transcribing locally..."]
    assert progress == [1]
    assert len(recorded_paths) == 1
    assert not recorded_paths[0].exists()


def test_public_transcribe_function_accepts_replaceable_backend(tmp_path) -> None:
    audio_path = tmp_path / "audio.wav"
    audio_path.write_bytes(b"audio")

    class FakeTranscriber:
        def transcribe_audio(self, path: str | Path) -> str:
            return f"transcribed {Path(path).name}"

    assert transcribe_audio(audio_path, transcriber=FakeTranscriber()) == "transcribed audio.wav"
