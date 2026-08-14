from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from models.criteria import RecipientCriteria
from models.direct_message import DirectMessage
from parsing import RecipientParser, RuleBasedRecipientParser

from .actions import Action
from .events import WorkerEvent, WorkerEventType
from .jobs import WorkflowRunner
from .message_generator import MessageGenerator, StubMessageGenerator
from .search_contract import ProfileCandidate, SearchResult


class AppState(Enum):
    HOME = auto()
    RECORD_RECIPIENT = auto()
    TRANSCRIBING = auto()
    RECIPIENT_REVIEW = auto()
    REFINE_RECIPIENT = auto()
    SEARCHING = auto()
    SELECT_PROFILE = auto()
    RECORD_MESSAGE = auto()
    REVIEW_MESSAGE = auto()
    REFINE_MESSAGE = auto()
    SENDING = auto()
    SUCCESS = auto()
    ERROR = auto()

    # Compatibility names retained for the earlier device milestone.
    RECIPIENT_RECORDING = RECORD_RECIPIENT
    CRITERIA_REVIEW = RECIPIENT_REVIEW
    RECIPIENT_REFINEMENT = REFINE_RECIPIENT
    PROFILE_RESULTS = SELECT_PROFILE
    MESSAGE_RECORDING = RECORD_MESSAGE


@dataclass(slots=True)
class SessionContext:
    transcript: str = ""
    criteria: RecipientCriteria = field(default_factory=RecipientCriteria)
    search_query: str = ""
    candidates: list[ProfileCandidate] = field(default_factory=list)
    selected_index: int = 0
    selected_candidate: ProfileCandidate | None = None
    message_transcript: str = ""
    message_draft: str = ""
    message_sent: bool = False
    sent_message: DirectMessage | None = None
    criteria_scroll: int = 0
    profile_scroll: int = 0
    message_scroll: int = 0
    current_operation_id: int | None = None
    current_operation: str = ""
    recording_started_at: float | None = None
    recording_remaining: int = 0
    refinement_origin: AppState = AppState.RECIPIENT_REVIEW
    recoverable_error: str = ""
    error_operation: str = ""
    error_back_state: AppState = AppState.HOME


class DeviceController:
    """Hardware-, UI-, and search-provider-independent application state machine."""

    def __init__(
        self,
        runner: WorkflowRunner,
        *,
        record_limit_seconds: float = 8.0,
        clock: Callable[[], float] = time.monotonic,
        parser: RecipientParser | None = None,
        message_generator: MessageGenerator | None = None,
    ) -> None:
        self.runner = runner
        self.record_limit_seconds = record_limit_seconds
        self.clock = clock
        self.parser = parser or RuleBasedRecipientParser()
        self.message_generator = message_generator or StubMessageGenerator()
        self.state = AppState.HOME
        self.context = SessionContext(recording_remaining=int(record_limit_seconds))
        self.running = True

    def dispatch(self, action: Action) -> None:
        if action is Action.QUIT:
            self.running = False
            self.runner.cancel_current()
            return

        handlers = {
            AppState.HOME: self._handle_home,
            AppState.RECORD_RECIPIENT: self._handle_recipient_recording,
            AppState.TRANSCRIBING: self._handle_transcribing,
            AppState.RECIPIENT_REVIEW: self._handle_recipient_review,
            AppState.REFINE_RECIPIENT: self._handle_recipient_refinement,
            AppState.SEARCHING: self._handle_searching,
            AppState.SELECT_PROFILE: self._handle_profiles,
            AppState.RECORD_MESSAGE: self._handle_message_recording,
            AppState.REVIEW_MESSAGE: self._handle_message_review,
            AppState.REFINE_MESSAGE: self._handle_message_refinement,
            AppState.SENDING: self._handle_sending,
            AppState.SUCCESS: self._handle_success,
            AppState.ERROR: self._handle_error,
        }
        handlers[self.state](action)

    def update(self) -> None:
        for event in self.runner.poll():
            if event.operation_id != self.context.current_operation_id:
                continue
            self._handle_worker_event(event)

    @property
    def recording_elapsed(self) -> float:
        if self.context.recording_started_at is None:
            return 0.0
        return min(
            self.record_limit_seconds,
            max(0.0, self.clock() - self.context.recording_started_at),
        )

    @property
    def current_candidate(self) -> ProfileCandidate | None:
        if not self.context.candidates:
            return None
        return self.context.candidates[self.context.selected_index]

    def _handle_home(self, action: Action) -> None:
        if action is Action.SEND_DOWN:
            self.context = SessionContext(
                recording_remaining=int(self.record_limit_seconds)
            )
            self._start_voice("initial_voice", AppState.RECORD_RECIPIENT)

    def _handle_recipient_recording(self, action: Action) -> None:
        if action is Action.SEND_UP:
            self._finish_recording()
        elif action in {Action.REFINE, Action.BACK}:
            self._cancel_to(AppState.HOME)

    def _handle_transcribing(self, action: Action) -> None:
        if action not in {Action.BACK, Action.REFINE}:
            return
        targets = {
            "initial_voice": AppState.HOME,
            "recipient_refinement": self.context.refinement_origin,
            "message_voice": AppState.RECORD_MESSAGE,
            "message_refinement": AppState.REVIEW_MESSAGE,
        }
        self._cancel_to(targets.get(self.context.current_operation, AppState.HOME))

    def _handle_recipient_review(self, action: Action) -> None:
        if action is Action.SEND_DOWN:
            self._start_search()
        elif action is Action.REFINE:
            self.context.refinement_origin = AppState.RECIPIENT_REVIEW
            self.context.criteria_scroll = 0
            self.state = AppState.REFINE_RECIPIENT
        elif action is Action.SCROLL_UP:
            self.context.criteria_scroll = max(0, self.context.criteria_scroll - 1)
        elif action is Action.SCROLL_DOWN:
            self.context.criteria_scroll += 1
        elif action is Action.BACK:
            self._cancel_to(AppState.HOME)

    def _handle_recipient_refinement(self, action: Action) -> None:
        if action is Action.SEND_DOWN and self.context.current_operation_id is None:
            self._start_voice("recipient_refinement", AppState.REFINE_RECIPIENT)
        elif action is Action.SEND_UP and self.context.current_operation_id is not None:
            self._finish_recording()
        elif action in {Action.BACK, Action.REFINE}:
            self._cancel_to(self.context.refinement_origin)

    def _handle_searching(self, action: Action) -> None:
        if action is Action.REFINE:
            self.runner.cancel_current()
            self.context.current_operation_id = None
            self.context.refinement_origin = AppState.RECIPIENT_REVIEW
            self.state = AppState.REFINE_RECIPIENT
        elif action is Action.BACK:
            self._cancel_to(AppState.RECIPIENT_REVIEW)

    def _handle_profiles(self, action: Action) -> None:
        count = len(self.context.candidates)
        if action is Action.PREVIOUS and count:
            self.context.selected_index = (self.context.selected_index - 1) % count
            self.context.profile_scroll = 0
        elif action is Action.NEXT and count:
            self.context.selected_index = (self.context.selected_index + 1) % count
            self.context.profile_scroll = 0
        elif action is Action.SCROLL_UP:
            self.context.profile_scroll = max(0, self.context.profile_scroll - 1)
        elif action is Action.SCROLL_DOWN:
            self.context.profile_scroll += 1
        elif action is Action.REFINE:
            self.context.refinement_origin = AppState.SELECT_PROFILE
            self.state = AppState.REFINE_RECIPIENT
        elif action is Action.SEND_DOWN and self.current_candidate is not None:
            self.context.selected_candidate = self.current_candidate
            self.context.message_transcript = ""
            self.context.message_draft = ""
            self.state = AppState.RECORD_MESSAGE
        elif action is Action.BACK:
            self._cancel_to(AppState.RECIPIENT_REVIEW)

    def _handle_message_recording(self, action: Action) -> None:
        if action is Action.SEND_DOWN and self.context.current_operation_id is None:
            self._start_voice("message_voice", AppState.RECORD_MESSAGE)
        elif action is Action.SEND_UP and self.context.current_operation_id is not None:
            self._finish_recording()
        elif action is Action.BACK:
            self._cancel_to(AppState.SELECT_PROFILE)

    def _handle_message_review(self, action: Action) -> None:
        if action is Action.SEND_DOWN:
            self._start_message()
        elif action is Action.REFINE:
            self.state = AppState.REFINE_MESSAGE
        elif action is Action.SCROLL_UP:
            self.context.message_scroll = max(0, self.context.message_scroll - 1)
        elif action is Action.SCROLL_DOWN:
            self.context.message_scroll += 1
        elif action is Action.BACK:
            self.state = AppState.RECORD_MESSAGE

    def _handle_message_refinement(self, action: Action) -> None:
        if action is Action.SEND_DOWN and self.context.current_operation_id is None:
            self._start_voice("message_refinement", AppState.REFINE_MESSAGE)
        elif action is Action.SEND_UP and self.context.current_operation_id is not None:
            self._finish_recording()
        elif action in {Action.BACK, Action.REFINE}:
            self._cancel_to(AppState.REVIEW_MESSAGE)

    def _handle_sending(self, action: Action) -> None:
        # Once confirmed, delivery may already have committed. Ignore buttons
        # until the provider reports success or failure rather than implying
        # that an in-flight send can be safely cancelled.
        return

    def _handle_success(self, action: Action) -> None:
        if action in {Action.SEND_DOWN, Action.BACK}:
            self.context = SessionContext(
                recording_remaining=int(self.record_limit_seconds)
            )
            self.state = AppState.HOME

    def _handle_error(self, action: Action) -> None:
        if action is Action.SEND_DOWN:
            operation = self.context.error_operation
            self.context.recoverable_error = ""
            if operation == "search":
                self._start_search()
            elif operation == "send_message":
                self._start_message()
            else:
                states = {
                    "initial_voice": AppState.RECORD_RECIPIENT,
                    "recipient_refinement": AppState.REFINE_RECIPIENT,
                    "message_voice": AppState.RECORD_MESSAGE,
                    "message_refinement": AppState.REFINE_MESSAGE,
                }
                self._start_voice(operation or "initial_voice", states.get(operation, AppState.RECORD_RECIPIENT))
        elif action is Action.REFINE and self.context.criteria.name:
            self.context.refinement_origin = AppState.RECIPIENT_REVIEW
            self.state = AppState.REFINE_RECIPIENT
        elif action is Action.BACK:
            self._cancel_to(self.context.error_back_state)

    def _start_voice(self, operation: str, state: AppState) -> None:
        operation_id = self.runner.start_voice(operation)
        self.context.current_operation_id = operation_id
        self.context.current_operation = operation
        self.context.recording_started_at = self.clock()
        self.context.recording_remaining = int(self.record_limit_seconds)
        self.context.recoverable_error = ""
        self.state = state

    def _finish_recording(self) -> None:
        self.runner.stop_voice()
        self.state = AppState.TRANSCRIBING

    def _start_search(self) -> None:
        operation_id = self.runner.start_search(self.context.criteria)
        self.context.current_operation_id = operation_id
        self.context.current_operation = "search"
        self.context.recoverable_error = ""
        self.state = AppState.SEARCHING

    def _start_message(self) -> None:
        recipient = self.context.selected_candidate
        if recipient is None or not self.context.message_draft.strip():
            self._fail_locally("A recipient and message are required.", "send_message")
            return
        operation_id = self.runner.start_message(
            recipient.id,
            self.context.message_draft,
        )
        self.context.current_operation_id = operation_id
        self.context.current_operation = "send_message"
        self.context.recoverable_error = ""
        self.state = AppState.SENDING

    def _cancel_to(self, state: AppState) -> None:
        self.runner.cancel_current()
        self.context.current_operation_id = None
        self.context.current_operation = ""
        self.context.recording_started_at = None
        self.state = state

    def _handle_worker_event(self, event: WorkerEvent) -> None:
        if event.type is WorkerEventType.RECORDING_PROGRESS:
            self.context.recording_remaining = int(event.payload)
            return
        if event.type is WorkerEventType.RECORDING_FINISHED:
            if self.state in {
                AppState.RECORD_RECIPIENT,
                AppState.REFINE_RECIPIENT,
                AppState.RECORD_MESSAGE,
                AppState.REFINE_MESSAGE,
            }:
                self.state = AppState.TRANSCRIBING
            return
        if event.type is WorkerEventType.VOICE_COMPLETE:
            self._handle_voice_complete(event.operation, str(event.payload))
            return
        if event.type is WorkerEventType.SEARCH_COMPLETE:
            self._handle_search_complete(event.payload)
            return
        if event.type is WorkerEventType.MESSAGE_SENT:
            self.context.sent_message = event.payload
            self.context.message_sent = True
            self._clear_operation()
            self.state = AppState.SUCCESS
            return
        if event.type is WorkerEventType.FAILED:
            self._handle_failure(event)

    def _handle_voice_complete(self, operation: str, transcript: str) -> None:
        self._clear_operation()
        if operation == "initial_voice":
            self.context.transcript = transcript
            self.context.criteria = self.parser.parse(transcript)
            self.context.candidates = []
            self.context.selected_candidate = None
            self.context.criteria_scroll = 0
            self.state = AppState.RECIPIENT_REVIEW
            return
        if operation in {"recipient_refinement", "refinement_voice"}:
            self.context.transcript = (
                f"{self.context.transcript}\nRefinement: {transcript}".strip()
            )
            self.context.criteria = self.parser.refine(self.context.criteria, transcript)
            self.context.criteria_scroll = 0
            if self.context.refinement_origin is AppState.SELECT_PROFILE:
                self._start_search()
            else:
                self.state = AppState.RECIPIENT_REVIEW
            return

        recipient = self.context.selected_candidate
        if recipient is None:
            self._fail_locally("Selected recipient is no longer available.", operation)
            return
        if operation == "message_voice":
            self.context.message_transcript = transcript
            self.context.message_draft = self.message_generator.generate(transcript, recipient)
        else:
            self.context.message_draft = self.message_generator.refine(
                self.context.message_draft,
                transcript,
                recipient,
            )
        self.context.message_scroll = 0
        self.state = AppState.REVIEW_MESSAGE

    def _handle_search_complete(self, payload: Any) -> None:
        if isinstance(payload, SearchResult):
            self.context.search_query = payload.query
            self.context.candidates = list(payload.candidates)
        else:
            self.context.search_query = ""
            self.context.candidates = [
                _coerce_candidate(candidate) for candidate in list(payload or [])[:10]
            ]
        self.context.selected_index = 0
        self.context.profile_scroll = 0
        self._clear_operation()
        self.state = AppState.SELECT_PROFILE

    def _handle_failure(self, event: WorkerEvent) -> None:
        self.context.recoverable_error = str(event.payload)
        self.context.error_operation = event.operation
        back_states = {
            "initial_voice": AppState.HOME,
            "search": AppState.RECIPIENT_REVIEW,
            "recipient_refinement": self.context.refinement_origin,
            "message_voice": AppState.RECORD_MESSAGE,
            "message_refinement": AppState.REVIEW_MESSAGE,
            "send_message": AppState.REVIEW_MESSAGE,
        }
        self.context.error_back_state = back_states.get(event.operation, AppState.HOME)
        self._clear_operation()
        self.state = AppState.ERROR

    def _fail_locally(self, message: str, operation: str) -> None:
        self._handle_failure(
            WorkerEvent(-1, WorkerEventType.FAILED, message, operation)
        )

    def _clear_operation(self) -> None:
        self.context.current_operation_id = None
        self.context.current_operation = ""
        self.context.recording_started_at = None


def _coerce_candidate(candidate: Any) -> ProfileCandidate:
    if isinstance(candidate, ProfileCandidate):
        return candidate
    if hasattr(candidate, "to_dict"):
        data = candidate.to_dict()
    elif isinstance(candidate, dict):
        data = candidate
    else:
        raise TypeError("Search candidates must be ProfileCandidate or mapping-like.")
    username = str(data.get("username", "") or "").lstrip("@")
    data = {
        **data,
        "username": username,
        "profile_url": data.get("profile_url") or f"https://x.com/{username}",
    }
    return ProfileCandidate.from_dict(data)
