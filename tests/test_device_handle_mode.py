from __future__ import annotations

from models.criteria import RecipientCriteria

from device_app.actions import Action
from device_app.events import WorkerEvent, WorkerEventType
from device_app.search_contract import ProfileCandidate, SearchResult
from device_app.state import AppState, DeviceController

from tests.test_device_state import FakeRunner


class HandleRunner(FakeRunner):
    def __init__(self, result: SearchResult | None = None) -> None:
        super().__init__()
        self.lookup_calls: list[str] = []
        self.result = result

    def start_handle_lookup(self, handle: str) -> int:
        operation_id = self.next_id
        self.next_id += 1
        self.lookup_calls.append(handle)
        return operation_id


FOUND = SearchResult(
    query="@jbart",
    candidates=(
        ProfileCandidate(id="55", name="Joe Bart", username="jbart", profile_url="https://x.com/jbart"),
    ),
)


def speak(controller: DeviceController, runner: HandleRunner, transcript: str) -> None:
    operation_id = controller.context.current_operation_id
    assert operation_id is not None
    controller.dispatch(Action.SEND_UP)
    runner.emit(
        WorkerEvent(operation_id, WorkerEventType.VOICE_COMPLETE, transcript, "handle_voice")
    )
    controller.update()


def test_handle_mode_skips_the_search_loop_entirely() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)

    controller.dispatch(Action.HANDLE_MODE)
    assert controller.state is AppState.HANDLE_RECORDING
    assert runner.voice_calls == ["handle_voice"]

    speak(controller, runner, "his handle is jbart")
    assert controller.state is AppState.HANDLE_LOOKUP
    assert runner.lookup_calls == ["jbart"]
    # The point of the shortcut: no search was ever started.
    assert runner.search_calls == []

    operation_id = controller.context.current_operation_id
    assert operation_id is not None
    runner.emit(
        WorkerEvent(operation_id, WorkerEventType.SEARCH_COMPLETE, FOUND, "handle_lookup")
    )
    controller.update()

    assert controller.state is AppState.SELECT_PROFILE
    assert len(controller.context.candidates) == 1
    assert controller.current_candidate.username == "jbart"


def test_an_unspeakable_handle_stops_rather_than_becoming_a_search() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)

    controller.dispatch(Action.HANDLE_MODE)
    speak(controller, runner, "joe bart a member of technical staff at meta")

    # A deliberate handle request that cannot be parsed is an answer, not a
    # mis-detection to be quietly converted into a description search.
    assert controller.state is AppState.HANDLE_NOT_FOUND
    assert runner.lookup_calls == []
    assert runner.search_calls == []


def test_an_unresolvable_handle_lands_on_not_found() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)

    controller.dispatch(Action.HANDLE_MODE)
    speak(controller, runner, "at nobodyhome")
    operation_id = controller.context.current_operation_id
    assert operation_id is not None
    runner.emit(
        WorkerEvent(
            operation_id,
            WorkerEventType.SEARCH_COMPLETE,
            SearchResult(query="@nobodyhome", candidates=()),
            "handle_lookup",
        )
    )
    controller.update()

    assert controller.state is AppState.HANDLE_NOT_FOUND
    assert controller.context.handle == "nobodyhome"


def test_not_found_offers_both_ways_forward() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)
    controller.dispatch(Action.HANDLE_MODE)
    speak(controller, runner, "not a handle at all because it is far too long")

    controller.dispatch(Action.SEND_DOWN)
    assert controller.state is AppState.RECORD_RECIPIENT
    assert runner.voice_calls[-1] == "initial_voice"


def test_the_profile_screen_can_switch_to_the_handle_as_a_recovery_path() -> None:
    runner = HandleRunner()
    controller = DeviceController(runner)
    controller.context.criteria = RecipientCriteria(name="Joe Bart")
    controller.state = AppState.SELECT_PROFILE

    controller.dispatch(Action.HANDLE_MODE)

    assert controller.state is AppState.HANDLE_RECORDING
