from __future__ import annotations

from voice.errors import VoiceInputError
from voice.recorder import available_input_devices


def main() -> int:
    try:
        devices = available_input_devices()
    except VoiceInputError as exc:
        print(f"Error: {exc}")
        return 1
    if not devices:
        print("No microphone input devices were found.")
        return 1
    print("Available microphone input devices:")
    for index, name in devices:
        print(f"{index}: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
