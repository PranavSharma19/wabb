from __future__ import annotations

import sys

from config import load_settings


def main() -> int:
    settings = load_settings()
    try:
        from faster_whisper import download_model
    except ImportError:
        print(
            "Install voice dependencies first: "
            "python -m pip install -r requirements-voice.txt",
            file=sys.stderr,
        )
        return 1

    destination = settings.voice_model_path
    destination.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {settings.voice_model} to {destination} ...")
    try:
        download_model(settings.voice_model, output_dir=str(destination))
    except Exception as exc:
        print(f"Error: Could not download the Whisper model: {exc}", file=sys.stderr)
        return 1
    print("Model ready. Runtime transcription can now remain offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
