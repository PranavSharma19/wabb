from __future__ import annotations

from collections.abc import Iterable

from device_app.actions import Action


class KeyboardInput:
    """Translate Pygame keyboard events into hardware-neutral actions."""

    def translate(self, events: Iterable[object]) -> list[Action]:
        import pygame

        actions: list[Action] = []
        key_down = {
            pygame.K_SPACE: Action.SEND_DOWN,
            pygame.K_RETURN: Action.SEND_DOWN,
            pygame.K_KP_ENTER: Action.SEND_DOWN,
            pygame.K_r: Action.REFINE,
            pygame.K_LEFT: Action.PREVIOUS,
            pygame.K_RIGHT: Action.NEXT,
            pygame.K_UP: Action.SCROLL_UP,
            pygame.K_DOWN: Action.SCROLL_DOWN,
            pygame.K_ESCAPE: Action.BACK,
        }
        for event in events:
            if event.type == pygame.QUIT:
                actions.append(Action.QUIT)
            elif event.type == pygame.KEYDOWN and not getattr(event, "repeat", False):
                action = key_down.get(event.key)
                if action is not None:
                    actions.append(action)
            elif event.type == pygame.KEYUP and event.key in {
                pygame.K_SPACE,
                pygame.K_RETURN,
                pygame.K_KP_ENTER,
            }:
                actions.append(Action.SEND_UP)
        return actions
