from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any


class WorkerEventType(Enum):
    RECORDING_PROGRESS = auto()
    RECORDING_FINISHED = auto()
    VOICE_COMPLETE = auto()
    SEARCH_COMPLETE = auto()
    MESSAGE_SENT = auto()
    FAILED = auto()


@dataclass(frozen=True, slots=True)
class WorkerEvent:
    operation_id: int
    type: WorkerEventType
    payload: Any = None
    operation: str = ""
