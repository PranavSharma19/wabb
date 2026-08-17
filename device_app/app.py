from __future__ import annotations

import logging
import os

from config import Settings, load_settings
from messaging import DirectMessageClient
from mock_x_platform.client import MockXPlatformClient
from voice.service import OfflineVoiceInput

from .actions import Action
from .input import GPIOButtonInput, KeyboardInput, RPiGPIOBackend
from .jobs import ThreadedWorkflowRunner
from .message_delivery import LocalMockDirectMessageClient
from .search_contract import SearchProvider
from .search_providers import ExistingSearchAdapter, MockSearchProvider
from .state import DeviceController
from .ui import DeviceRenderer, HEIGHT, WIDTH


def run(
    settings: Settings | None = None,
    *,
    search_provider: SearchProvider | None = None,
    message_client: DirectMessageClient | None = None,
) -> int:
    import pygame

    active_settings = settings or load_settings()
    pygame.init()
    flags = pygame.FULLSCREEN if active_settings.device_fullscreen else 0
    surface = pygame.display.set_mode((WIDTH, HEIGHT), flags)
    pygame.display.set_caption("Voice-First Outreach Device")
    pygame.mouse.set_visible(not active_settings.device_fullscreen)

    voice_input = OfflineVoiceInput(active_settings)
    runner = ThreadedWorkflowRunner(
        voice_input,
        search_provider=search_provider or _configured_search_provider(active_settings),
        message_client=message_client or _configured_message_client(active_settings),
    )
    controller = DeviceController(
        runner,
        record_limit_seconds=active_settings.voice_record_seconds,
    )
    keyboard = KeyboardInput()
    gpio_input = (
        GPIOButtonInput(RPiGPIOBackend())
        if os.getenv("DEVICE_GPIO", "false").strip().casefold() in {"1", "true", "yes", "on"}
        else None
    )
    renderer = DeviceRenderer()
    frame_clock = pygame.time.Clock()

    try:
        while controller.running:
            for action in keyboard.translate(pygame.event.get()):
                controller.dispatch(action)
            if gpio_input is not None:
                for action in gpio_input.poll():
                    controller.dispatch(action)
            controller.update()
            renderer.render(surface, controller)
            pygame.display.flip()
            frame_clock.tick(active_settings.device_fps)
    finally:
        if gpio_input is not None:
            gpio_input.close()
        runner.shutdown()
        pygame.quit()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return run()


def _configured_search_provider(settings: Settings) -> SearchProvider:
    mode = os.getenv("DEVICE_SEARCH_PROVIDER", "mock").strip().casefold()
    if mode == "mock":
        return MockSearchProvider()
    if mode == "existing":
        # Import only when explicitly selected. Mock device development never
        # initializes or calls the real search implementation.
        from search.service import find_users, lookup_user

        return ExistingSearchAdapter(
            lambda criteria: find_users(criteria, settings=settings),
            lambda handle: lookup_user(handle, settings=settings),
        )
    raise ValueError("DEVICE_SEARCH_PROVIDER must be 'mock' or 'existing'.")


def _configured_message_client(settings: Settings) -> DirectMessageClient:
    mode = os.getenv("DEVICE_MESSAGE_PROVIDER", "mock").strip().casefold()
    if mode != "mock":
        raise ValueError("DEVICE_MESSAGE_PROVIDER must be 'mock'.")
    if settings.mock_x_base_url:
        return MockXPlatformClient(
            settings.mock_x_base_url,
            timeout_seconds=settings.request_timeout_seconds,
        )
    return LocalMockDirectMessageClient(settings.mock_x_database_path)
