from __future__ import annotations

import time

from device_app.events import WorkerEventType
from device_app.jobs import ThreadedWorkflowRunner
from device_app.search_contract import SearchResult
from models.candidate import Candidate
from models.criteria import RecipientCriteria
from models.direct_message import DirectMessage
from voice.errors import VoiceInputCancelled


class FakeVoiceInput:
    def listen(self, **kwargs) -> str:
        kwargs["on_progress"](8)
        while not kwargs["stop_requested"]():
            if kwargs["cancel_requested"]():
                raise VoiceInputCancelled("cancelled")
            time.sleep(0.001)
        kwargs["on_status"]("recording complete")
        if kwargs["cancel_requested"]():
            raise VoiceInputCancelled("cancelled")
        return "John Doe at XYZ"


class FakeMessageClient:
    def send_message(self, recipient_id: str, text: str) -> DirectMessage:
        return DirectMessage(
            id="1",
            conversation_id="conversation",
            sender_id="owner",
            recipient_id=recipient_id,
            text=text,
            created_at="2026-08-14T00:00:00Z",
        )

    def list_messages(self, participant_id: str) -> list[DirectMessage]:
        return []


def wait_for_events(runner: ThreadedWorkflowRunner, expected: WorkerEventType):
    deadline = time.monotonic() + 2
    collected = []
    while time.monotonic() < deadline:
        collected.extend(runner.poll())
        if any(event.type is expected for event in collected):
            return collected
        time.sleep(0.005)
    raise AssertionError(f"Timed out waiting for {expected}")


def test_threaded_runner_stops_voice_and_posts_typed_events() -> None:
    runner = ThreadedWorkflowRunner(FakeVoiceInput())  # type: ignore[arg-type]
    try:
        operation_id = runner.start_voice("initial_voice")
        wait_for_events(runner, WorkerEventType.RECORDING_PROGRESS)
        runner.stop_voice()
        events = wait_for_events(runner, WorkerEventType.VOICE_COMPLETE)

        assert all(event.operation_id == operation_id for event in events)
        assert any(event.type is WorkerEventType.RECORDING_FINISHED for event in events)
        complete = next(event for event in events if event.type is WorkerEventType.VOICE_COMPLETE)
        assert complete.payload == "John Doe at XYZ"
    finally:
        runner.shutdown()


def test_threaded_runner_search_returns_domain_candidates() -> None:
    expected = Candidate(id="1", name="John Doe", username="john")
    runner = ThreadedWorkflowRunner(
        FakeVoiceInput(),  # type: ignore[arg-type]
        finder=lambda _criteria: [expected],
    )
    try:
        operation_id = runner.start_search(RecipientCriteria(name="John Doe"))
        events = wait_for_events(runner, WorkerEventType.SEARCH_COMPLETE)
        result = next(event for event in events if event.type is WorkerEventType.SEARCH_COMPLETE)

        assert result.operation_id == operation_id
        assert isinstance(result.payload, SearchResult)
        assert result.payload.query == "John Doe"
        assert [candidate.id for candidate in result.payload.candidates] == [expected.id]
    finally:
        runner.shutdown()


def test_threaded_runner_posts_sent_message_event() -> None:
    runner = ThreadedWorkflowRunner(
        FakeVoiceInput(),  # type: ignore[arg-type]
        message_client=FakeMessageClient(),
    )
    try:
        operation_id = runner.start_message("1000001", "Hello John")
        events = wait_for_events(runner, WorkerEventType.MESSAGE_SENT)
        result = next(event for event in events if event.type is WorkerEventType.MESSAGE_SENT)

        assert result.operation_id == operation_id
        assert result.payload.recipient_id == "1000001"
        assert result.payload.text == "Hello John"
    finally:
        runner.shutdown()
