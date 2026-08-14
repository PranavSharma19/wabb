from __future__ import annotations

from enum import Enum, auto


class Action(Enum):
    """Hardware-neutral actions understood by the device state machine."""

    SEND_DOWN = auto()
    SEND_UP = auto()
    REFINE = auto()
    PREVIOUS = auto()
    NEXT = auto()
    SCROLL_UP = auto()
    SCROLL_DOWN = auto()
    BACK = auto()
    QUIT = auto()
