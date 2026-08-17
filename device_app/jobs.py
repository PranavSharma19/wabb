from __future__ import annotations

import itertools
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Protocol

from models.candidate import Candidate
from models.criteria import RecipientCriteria
from models.direct_message import DirectMessage
from messaging import DirectMessageClient
from voice.errors import VoiceInputCancelled
from voice.service import OfflineVoiceInput

from .events import WorkerEvent, WorkerEventType
from .search_contract import SearchProvider
from .search_providers import ExistingSearchAdapter, MockSearchProvider


class WorkflowRunner(Protocol):
    def start_voice(self, operation: str) -> int: ...
    def stop_voice(self) -> None: ...
    def cancel_current(self) -> None: ...
    def start_search(self, criteria: RecipientCriteria) -> int: ...
    def start_handle_lookup(self, handle: str) -> int: ...
    def start_message(self, recipient_id: str, text: str) -> int: ...
    def poll(self) -> list[WorkerEvent]: ...
    def shutdown(self) -> None: ...


class ThreadedWorkflowRunner:
    """Runs microphone, transcription, and provider work without blocking the UI."""

    def __init__(
        self,
        voice_input: OfflineVoiceInput,
        *,
        search_provider: SearchProvider | None = None,
        message_client: DirectMessageClient | None = None,
        finder: Callable[[RecipientCriteria], list[Candidate]] | None = None,
    ) -> None:
        if search_provider is not None and finder is not None:
            raise ValueError("Pass search_provider or finder, not both.")
        self.voice_input = voice_input
        self.search_provider = search_provider or (
            ExistingSearchAdapter(finder) if finder is not None else MockSearchProvider()
        )
        self.message_client = message_client
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="device-worker")
        self._events: queue.SimpleQueue[WorkerEvent] = queue.SimpleQueue()
        self._ids = itertools.count(1)
        self._voice_stop: threading.Event | None = None
        self._voice_cancel: threading.Event | None = None
        self._current_operation_id: int | None = None

    def start_voice(self, operation: str) -> int:
        self.cancel_current()
        operation_id = next(self._ids)
        stop_event = threading.Event()
        cancel_event = threading.Event()
        self._voice_stop = stop_event
        self._voice_cancel = cancel_event
        self._current_operation_id = operation_id
        self._executor.submit(
            self._run_voice,
            operation_id,
            operation,
            stop_event,
            cancel_event,
        )
        return operation_id

    def stop_voice(self) -> None:
        if self._voice_stop is not None:
            self._voice_stop.set()

    def cancel_current(self) -> None:
        if self._voice_cancel is not None:
            self._voice_cancel.set()
        if self._voice_stop is not None:
            self._voice_stop.set()
        self._current_operation_id = None

    def start_search(self, criteria: RecipientCriteria) -> int:
        self.cancel_current()
        operation_id = next(self._ids)
        self._current_operation_id = operation_id
        criteria_copy = RecipientCriteria.from_dict(criteria.to_dict())
        self._executor.submit(self._run_search, operation_id, criteria_copy)
        return operation_id

    def start_handle_lookup(self, handle: str) -> int:
        self.cancel_current()
        operation_id = next(self._ids)
        self._current_operation_id = operation_id
        self._executor.submit(self._run_handle_lookup, operation_id, str(handle))
        return operation_id

    def start_message(self, recipient_id: str, text: str) -> int:
        if self.message_client is None:
            raise RuntimeError("No direct-message client is configured.")
        self.cancel_current()
        operation_id = next(self._ids)
        self._current_operation_id = operation_id
        self._executor.submit(
            self._run_message,
            operation_id,
            str(recipient_id),
            str(text),
        )
        return operation_id

    def poll(self) -> list[WorkerEvent]:
        events: list[WorkerEvent] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def shutdown(self) -> None:
        self.cancel_current()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run_voice(
        self,
        operation_id: int,
        operation: str,
        stop_event: threading.Event,
        cancel_event: threading.Event,
    ) -> None:
        try:
            transcript = self.voice_input.listen(
                stop_requested=stop_event.is_set,
                cancel_requested=cancel_event.is_set,
                on_progress=lambda remaining: self._events.put(
                    WorkerEvent(
                        operation_id,
                        WorkerEventType.RECORDING_PROGRESS,
                        remaining,
                        operation,
                    )
                ),
                on_status=lambda _status: self._events.put(
                    WorkerEvent(
                        operation_id,
                        WorkerEventType.RECORDING_FINISHED,
                        operation=operation,
                    )
                ),
            )
        except VoiceInputCancelled:
            return
        except Exception as exc:
            self._events.put(
                WorkerEvent(operation_id, WorkerEventType.FAILED, str(exc), operation)
            )
            return
        self._events.put(
            WorkerEvent(operation_id, WorkerEventType.VOICE_COMPLETE, transcript, operation)
        )

    def _run_search(self, operation_id: int, criteria: RecipientCriteria) -> None:
        try:
            result = self.search_provider.search(criteria)
        except Exception as exc:
            self._events.put(
                WorkerEvent(operation_id, WorkerEventType.FAILED, str(exc), "search")
            )
            return
        self._events.put(
            WorkerEvent(operation_id, WorkerEventType.SEARCH_COMPLETE, result, "search")
        )

    def _run_handle_lookup(self, operation_id: int, handle: str) -> None:
        try:
            result = self.search_provider.lookup(handle)
        except Exception as exc:
            self._events.put(
                WorkerEvent(operation_id, WorkerEventType.FAILED, str(exc), "handle_lookup")
            )
            return
        # Reuses SEARCH_COMPLETE: a lookup is a search result of length zero or
        # one, and the profile screen already knows how to show one candidate.
        self._events.put(
            WorkerEvent(
                operation_id, WorkerEventType.SEARCH_COMPLETE, result, "handle_lookup"
            )
        )

    def _run_message(self, operation_id: int, recipient_id: str, text: str) -> None:
        try:
            assert self.message_client is not None
            message: DirectMessage = self.message_client.send_message(recipient_id, text)
        except Exception as exc:
            self._events.put(
                WorkerEvent(operation_id, WorkerEventType.FAILED, str(exc), "send_message")
            )
            return
        self._events.put(
            WorkerEvent(operation_id, WorkerEventType.MESSAGE_SENT, message, "send_message")
        )
