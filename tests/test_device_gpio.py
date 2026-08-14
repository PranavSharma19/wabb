from __future__ import annotations

from device_app.actions import Action
from device_app.input.gpio import ButtonPins, GPIOButtonInput


class FakeGPIO:
    def __init__(self) -> None:
        self.values: dict[int, bool] = {}
        self.setup: list[tuple[int, bool]] = []
        self.cleaned: tuple[int, ...] = ()

    def setup_input(self, pin: int, *, pull_up: bool) -> None:
        self.setup.append((pin, pull_up))
        self.values.setdefault(pin, True)

    def read(self, pin: int) -> bool:
        return self.values[pin]

    def cleanup(self, pins: tuple[int, ...]) -> None:
        self.cleaned = pins


def test_gpio_adapter_debounces_send_press_and_release() -> None:
    backend = FakeGPIO()
    now = [0.0]
    adapter = GPIOButtonInput(backend, clock=lambda: now[0], debounce_seconds=0.04)
    send_pin = ButtonPins().send

    backend.values[send_pin] = False
    assert adapter.poll() == []
    now[0] = 0.05
    assert adapter.poll() == [Action.SEND_DOWN]
    backend.values[send_pin] = True
    assert adapter.poll() == []
    now[0] = 0.10
    assert adapter.poll() == [Action.SEND_UP]


def test_gpio_adapter_emits_navigation_only_on_press_and_cleans_up() -> None:
    backend = FakeGPIO()
    now = [0.0]
    pins = ButtonPins()
    adapter = GPIOButtonInput(backend, pins=pins, clock=lambda: now[0], debounce_seconds=0.01)

    backend.values[pins.next] = False
    adapter.poll()
    now[0] = 0.02
    assert adapter.poll() == [Action.NEXT]
    backend.values[pins.next] = True
    adapter.poll()
    now[0] = 0.04
    assert adapter.poll() == []

    adapter.close()
    assert backend.cleaned == (pins.send, pins.refine, pins.previous, pins.next, pins.back)
