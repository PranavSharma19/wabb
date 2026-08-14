from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from device_app.actions import Action


class GPIOBackend(Protocol):
    def setup_input(self, pin: int, *, pull_up: bool) -> None: ...
    def read(self, pin: int) -> bool: ...
    def cleanup(self, pins: tuple[int, ...]) -> None: ...


@dataclass(frozen=True, slots=True)
class ButtonPins:
    send: int = 17
    refine: int = 27
    previous: int = 22
    next: int = 23
    back: int = 24

    def action_map(self) -> dict[int, Action]:
        return {
            self.send: Action.SEND_DOWN,
            self.refine: Action.REFINE,
            self.previous: Action.PREVIOUS,
            self.next: Action.NEXT,
            self.back: Action.BACK,
        }


class GPIOButtonInput:
    """Debounced active-low physical buttons translated to semantic actions."""

    def __init__(
        self,
        backend: GPIOBackend,
        *,
        pins: ButtonPins | None = None,
        debounce_seconds: float = 0.04,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend = backend
        self.pins = pins or ButtonPins()
        self.debounce_seconds = debounce_seconds
        self.clock = clock
        self._actions = self.pins.action_map()
        self._stable: dict[int, bool] = {}
        self._pending: dict[int, tuple[bool, float]] = {}
        for pin in self._actions:
            self.backend.setup_input(pin, pull_up=True)
            self._stable[pin] = self.backend.read(pin)

    def poll(self) -> list[Action]:
        now = self.clock()
        actions: list[Action] = []
        for pin, press_action in self._actions.items():
            raw = self.backend.read(pin)
            stable = self._stable[pin]
            if raw == stable:
                self._pending.pop(pin, None)
                continue
            pending = self._pending.get(pin)
            if pending is None or pending[0] != raw:
                self._pending[pin] = (raw, now)
                continue
            if now - pending[1] < self.debounce_seconds:
                continue
            self._stable[pin] = raw
            self._pending.pop(pin, None)
            pressed = not raw
            if pin == self.pins.send:
                actions.append(Action.SEND_DOWN if pressed else Action.SEND_UP)
            elif pressed:
                actions.append(press_action)
        return actions

    def close(self) -> None:
        self.backend.cleanup(tuple(self._actions))


class RPiGPIOBackend:
    """Lazy RPi.GPIO backend; importing this module remains safe on Windows."""

    def __init__(self) -> None:
        try:
            import RPi.GPIO as gpio
        except ImportError as exc:
            raise RuntimeError(
                "GPIO mode requires RPi.GPIO (or the compatible rpi-lgpio package)."
            ) from exc
        self.gpio = gpio
        self.gpio.setwarnings(False)
        self.gpio.setmode(self.gpio.BCM)

    def setup_input(self, pin: int, *, pull_up: bool) -> None:
        pull = self.gpio.PUD_UP if pull_up else self.gpio.PUD_DOWN
        self.gpio.setup(pin, self.gpio.IN, pull_up_down=pull)

    def read(self, pin: int) -> bool:
        return bool(self.gpio.input(pin))

    def cleanup(self, pins: tuple[int, ...]) -> None:
        self.gpio.cleanup(pins)
