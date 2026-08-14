from __future__ import annotations

import os

import pytest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
pygame = pytest.importorskip("pygame")

from device_app.actions import Action
from device_app.input.keyboard import KeyboardInput
from device_app.state import AppState, DeviceController
from device_app.ui import DeviceRenderer, HEIGHT, WIDTH
from models.candidate import Candidate

from tests.test_device_state import FakeRunner


def test_keyboard_adapter_emits_semantic_press_and_release_actions() -> None:
    adapter = KeyboardInput()
    events = [
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_SPACE, repeat=False),
        pygame.event.Event(pygame.KEYUP, key=pygame.K_SPACE),
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r, repeat=False),
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT, repeat=False),
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_DOWN, repeat=False),
        pygame.event.Event(pygame.KEYDOWN, key=pygame.K_RETURN, repeat=False),
        pygame.event.Event(pygame.KEYUP, key=pygame.K_RETURN),
    ]

    assert adapter.translate(events) == [
        Action.SEND_DOWN,
        Action.SEND_UP,
        Action.REFINE,
        Action.PREVIOUS,
        Action.SCROLL_DOWN,
        Action.SEND_DOWN,
        Action.SEND_UP,
    ]


def test_every_device_screen_renders_at_exact_resolution() -> None:
    pygame.init()
    surface = pygame.Surface((WIDTH, HEIGHT))
    renderer = DeviceRenderer()
    controller = DeviceController(FakeRunner())
    controller.context.transcript = "John Doe, recruiter at XYZ in Toronto"
    controller.context.criteria.name = "John Doe"
    controller.context.criteria.company = "XYZ"
    controller.context.candidates = [
        Candidate(
            id="1",
            name="John Doe",
            username="johndoe",
            bio="Recruiter at XYZ who works with product and engineering teams.",
            location="Toronto",
            can_dm=True,
            score=100,
            match_reasons=["+40 Exact name", "+30 Company found in profile"],
        )
    ]
    controller.context.selected_candidate = controller.context.candidates[0]
    controller.context.recoverable_error = "Microphone unavailable"

    for state in AppState:
        controller.state = state
        renderer.render(surface, controller, now=1.0)
        assert surface.get_size() == (800, 480)
    pygame.quit()


def test_renderer_rejects_resizable_desktop_dimensions() -> None:
    pygame.init()
    renderer = DeviceRenderer()
    controller = DeviceController(FakeRunner())

    with pytest.raises(ValueError, match="exactly 800x480"):
        renderer.render(pygame.Surface((1024, 768)), controller)
    pygame.quit()
