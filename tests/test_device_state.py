from __future__ import annotations

from models.candidate import Candidate
from models.criteria import RecipientCriteria
from models.direct_message import DirectMessage

from device_app.actions import Action
from device_app.events import WorkerEvent, WorkerEventType
from device_app.search_contract import ProfileCandidate, SearchResult
from device_app.state import AppState, DeviceController


class FakeRunner:
    def __init__(self) -> None:
        self.next_id = 1
        self.events: list[WorkerEvent] = []
        self.voice_calls: list[str] = []
        self.search_calls: list[RecipientCriteria] = []
        self.message_calls: list[tuple[str, str]] = []
        self.stop_calls = 0
        self.cancel_calls = 0

    def start_voice(self, operation: str) -> int:
        operation_id = self.next_id
        self.next_id += 1
        self.voice_calls.append(operation)
        return operation_id

    def stop_voice(self) -> None:
        self.stop_calls += 1

    def cancel_current(self) -> None:
        self.cancel_calls += 1

    def start_search(self, criteria: RecipientCriteria) -> int:
        operation_id = self.next_id
        self.next_id += 1
        self.search_calls.append(RecipientCriteria.from_dict(criteria.to_dict()))
        return operation_id

    def start_message(self, recipient_id: str, text: str) -> int:
        operation_id = self.next_id
        self.next_id += 1
        self.message_calls.append((recipient_id, text))
        return operation_id

    def poll(self) -> list[WorkerEvent]:
        events, self.events = self.events, []
        return events

    def shutdown(self) -> None:
        return None

    def emit(self, event: WorkerEvent) -> None:
        self.events.append(event)


def complete_initial_voice(controller: DeviceController, runner: FakeRunner) -> None:
    controller.dispatch(Action.SEND_DOWN)
    operation_id = controller.context.current_operation_id
    assert operation_id is not None
    controller.dispatch(Action.SEND_UP)
    runner.emit(
        WorkerEvent(
            operation_id,
            WorkerEventType.VOICE_COMPLETE,
            "John Doe, recruiter at XYZ in Toronto",
            "initial_voice",
        )
    )
    controller.update()


def test_hold_release_voice_then_confirm_before_search() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)

    controller.dispatch(Action.SEND_DOWN)
    assert controller.state is AppState.RECIPIENT_RECORDING
    assert runner.voice_calls == ["initial_voice"]
    assert runner.search_calls == []

    operation_id = controller.context.current_operation_id
    controller.dispatch(Action.SEND_UP)
    assert controller.state is AppState.TRANSCRIBING
    assert runner.stop_calls == 1

    runner.emit(
        WorkerEvent(
            operation_id,
            WorkerEventType.VOICE_COMPLETE,
            "John Doe, recruiter at XYZ in Toronto",
            "initial_voice",
        )
    )
    controller.update()
    assert controller.state is AppState.CRITERIA_REVIEW
    assert controller.context.criteria.company == "XYZ"
    assert runner.search_calls == []

    controller.dispatch(Action.SEND_DOWN)
    assert controller.state is AppState.SEARCHING
    assert runner.search_calls[0].name == "John Doe"


def test_recording_timeout_event_moves_to_transcribing() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)
    controller.dispatch(Action.SEND_DOWN)
    operation_id = controller.context.current_operation_id

    runner.emit(
        WorkerEvent(
            operation_id,
            WorkerEventType.RECORDING_FINISHED,
            operation="initial_voice",
        )
    )
    controller.update()

    assert controller.state is AppState.TRANSCRIBING


def test_spoken_refinement_returns_to_review_without_searching() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)
    complete_initial_voice(controller, runner)

    controller.dispatch(Action.REFINE)
    assert controller.state is AppState.RECIPIENT_REFINEMENT
    controller.dispatch(Action.SEND_DOWN)
    operation_id = controller.context.current_operation_id
    controller.dispatch(Action.SEND_UP)
    runner.emit(
        WorkerEvent(
            operation_id,
            WorkerEventType.VOICE_COMPLETE,
            "change company to ABC",
            "refinement_voice",
        )
    )
    controller.update()

    assert controller.state is AppState.CRITERIA_REVIEW
    assert controller.context.criteria.company == "ABC"
    assert len(runner.search_calls) == 0


def test_stale_completion_is_ignored_after_cancel() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)
    controller.dispatch(Action.SEND_DOWN)
    stale_id = controller.context.current_operation_id
    controller.dispatch(Action.BACK)

    runner.emit(
        WorkerEvent(
            stale_id,
            WorkerEventType.VOICE_COMPLETE,
            "Wrong Person",
            "initial_voice",
        )
    )
    controller.update()

    assert controller.state is AppState.HOME
    assert controller.context.criteria == RecipientCriteria()


def test_candidate_navigation_wraps_and_selection_is_preserved() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)
    controller.context.candidates = [
        Candidate(id="1", name="First", username="first"),
        Candidate(id="2", name="Second", username="second"),
    ]
    controller.state = AppState.PROFILE_RESULTS

    controller.dispatch(Action.PREVIOUS)
    assert controller.current_candidate is not None
    assert controller.current_candidate.id == "2"
    controller.dispatch(Action.NEXT)
    assert controller.current_candidate.id == "1"
    controller.dispatch(Action.NEXT)
    controller.dispatch(Action.SEND_DOWN)

    assert controller.state is AppState.MESSAGE_RECORDING
    assert controller.context.selected_candidate is not None
    assert controller.context.selected_candidate.id == "2"


def test_search_failure_is_recoverable_and_retryable() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)
    complete_initial_voice(controller, runner)
    controller.dispatch(Action.SEND_DOWN)
    operation_id = controller.context.current_operation_id
    runner.emit(
        WorkerEvent(operation_id, WorkerEventType.FAILED, "network unavailable", "search")
    )
    controller.update()
    assert controller.state is AppState.ERROR

    controller.dispatch(Action.SEND_DOWN)
    assert controller.state is AppState.SEARCHING
    assert len(runner.search_calls) == 2


def test_profile_refinement_reruns_search_with_accumulated_clues() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)
    complete_initial_voice(controller, runner)
    controller.dispatch(Action.SEND_DOWN)
    search_id = controller.context.current_operation_id
    runner.emit(
        WorkerEvent(
            search_id,
            WorkerEventType.SEARCH_COMPLETE,
            SearchResult(
                query="John Doe XYZ Toronto",
                candidates=(
                    ProfileCandidate(id="1", name="John Doe", username="john"),
                ),
            ),
            "search",
        )
    )
    controller.update()

    controller.dispatch(Action.REFINE)
    controller.dispatch(Action.SEND_DOWN)
    refinement_id = controller.context.current_operation_id
    controller.dispatch(Action.SEND_UP)
    runner.emit(
        WorkerEvent(
            refinement_id,
            WorkerEventType.VOICE_COMPLETE,
            "also went to U of T",
            "recipient_refinement",
        )
    )
    controller.update()

    assert controller.state is AppState.SEARCHING
    assert controller.context.criteria.school == "University of Toronto"
    assert len(runner.search_calls) == 2
    assert runner.search_calls[-1].company == "XYZ"


def test_end_to_end_message_record_review_refine_and_success() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)
    controller.context.candidates = [
        ProfileCandidate(id="1", name="John Doe", username="john")
    ]
    controller.state = AppState.SELECT_PROFILE

    controller.dispatch(Action.SEND_DOWN)
    assert controller.state is AppState.RECORD_MESSAGE
    controller.dispatch(Action.SEND_DOWN)
    message_id = controller.context.current_operation_id
    controller.dispatch(Action.SEND_UP)
    runner.emit(
        WorkerEvent(
            message_id,
            WorkerEventType.VOICE_COMPLETE,
            "hello John great to meet you and discuss the role",
            "message_voice",
        )
    )
    controller.update()

    assert controller.state is AppState.REVIEW_MESSAGE
    assert controller.context.message_draft.endswith(".")

    controller.dispatch(Action.REFINE)
    assert controller.state is AppState.REFINE_MESSAGE
    controller.dispatch(Action.SEND_DOWN)
    refinement_id = controller.context.current_operation_id
    controller.dispatch(Action.SEND_UP)
    runner.emit(
        WorkerEvent(
            refinement_id,
            WorkerEventType.VOICE_COMPLETE,
            "replace with hi John would you be open to chatting",
            "message_refinement",
        )
    )
    controller.update()

    assert controller.state is AppState.REVIEW_MESSAGE
    assert controller.context.message_draft == "Hi John would you be open to chatting."
    controller.dispatch(Action.SEND_DOWN)
    assert controller.state is AppState.SENDING
    send_id = controller.context.current_operation_id
    runner.emit(
        WorkerEvent(
            send_id,
            WorkerEventType.MESSAGE_SENT,
            DirectMessage(
                id="message-1",
                conversation_id="conversation-1",
                sender_id="owner",
                recipient_id="1",
                text=controller.context.message_draft,
                created_at="2026-08-14T00:00:00Z",
            ),
            "send_message",
        )
    )
    controller.update()
    assert controller.state is AppState.SUCCESS
    assert controller.context.message_sent is True
    assert controller.context.sent_message is not None
    assert runner.message_calls == [("1", "Hi John would you be open to chatting.")]


def test_message_failure_returns_to_review_and_can_retry() -> None:
    runner = FakeRunner()
    controller = DeviceController(runner)
    controller.context.selected_candidate = ProfileCandidate(
        id="1000001", name="John Doe", username="john"
    )
    controller.context.message_draft = "Hello John."
    controller.state = AppState.REVIEW_MESSAGE

    controller.dispatch(Action.SEND_DOWN)
    failed_id = controller.context.current_operation_id
    runner.emit(
        WorkerEvent(
            failed_id,
            WorkerEventType.FAILED,
            "mock service unavailable",
            "send_message",
        )
    )
    controller.update()

    assert controller.state is AppState.ERROR
    assert controller.context.error_back_state is AppState.REVIEW_MESSAGE
    controller.dispatch(Action.SEND_DOWN)
    assert controller.state is AppState.SENDING
    assert len(runner.message_calls) == 2
