from __future__ import annotations

import threading
import time
from dataclasses import replace

from config import load_settings
from device_app.actions import Action
from device_app.jobs import ThreadedWorkflowRunner
from device_app.search_providers import ExistingSearchAdapter
from device_app.state import AppState, DeviceController
from mock_x_platform.client import MockXPlatformClient
from mock_x_platform.dataset import build_dataset
from mock_x_platform.server import create_server
from search.service import find_users


class ScriptedVoiceInput:
    """Deterministic microphone/transcriber replacement for the full flow."""

    def __init__(self) -> None:
        self._responses = iter(
            (
                "John Doe, recruiter at XYZ in Toronto",
                "Hi John, I would love to connect and learn more about the role",
            )
        )

    def listen(self, **callbacks: object) -> str:
        on_status = callbacks["on_status"]
        assert callable(on_status)
        on_status("recording complete")
        return next(self._responses)


def wait_for_state(
    controller: DeviceController,
    expected: AppState,
    *,
    timeout_seconds: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        controller.update()
        if controller.state is expected:
            return
        if controller.state is AppState.ERROR:
            raise AssertionError(controller.context.recoverable_error)
        time.sleep(0.005)
    raise AssertionError(
        f"Timed out waiting for {expected.name}; current state is {controller.state.name}."
    )


def test_real_adapter_to_http_search_to_mock_dm_device_flow(tmp_path) -> None:
    """Lock the complete offline production-shaped path into one regression test."""

    database_path = tmp_path / "mock-x.sqlite3"
    assert build_dataset(database_path, count=1_000, seed=20260814) == 1_000
    server = create_server("127.0.0.1", 0, database_path=database_path)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"

    settings = replace(
        load_settings(),
        mock_x=True,
        mock_x_base_url=base_url,
        mock_x_database_path=database_path,
        cache_path=tmp_path / "search-cache.json",
        request_timeout_seconds=2.0,
    )
    provider = ExistingSearchAdapter(
        lambda criteria: find_users(criteria, settings=settings)
    )
    message_client = MockXPlatformClient(base_url, timeout_seconds=2.0)
    runner = ThreadedWorkflowRunner(
        ScriptedVoiceInput(),  # type: ignore[arg-type]
        search_provider=provider,
        message_client=message_client,
    )
    controller = DeviceController(runner)

    try:
        controller.dispatch(Action.SEND_DOWN)
        controller.dispatch(Action.SEND_UP)
        wait_for_state(controller, AppState.RECIPIENT_REVIEW)

        controller.dispatch(Action.SEND_DOWN)
        wait_for_state(controller, AppState.SELECT_PROFILE)
        candidate = controller.current_candidate
        assert controller.context.search_query == "John Doe XYZ Recruiter Toronto"
        assert len(controller.context.candidates) == 10
        assert candidate is not None
        assert candidate.id == "1000001"
        assert candidate.username == "johndoe_xyz"
        assert candidate.can_dm is True

        controller.dispatch(Action.SEND_DOWN)
        assert controller.state is AppState.RECORD_MESSAGE
        controller.dispatch(Action.SEND_DOWN)
        controller.dispatch(Action.SEND_UP)
        wait_for_state(controller, AppState.REVIEW_MESSAGE)
        assert controller.context.message_draft == (
            "Hi John, I would love to connect and learn more about the role."
        )

        controller.dispatch(Action.SEND_DOWN)
        assert controller.state is AppState.SENDING
        wait_for_state(controller, AppState.SUCCESS)

        sent = controller.context.sent_message
        assert sent is not None
        assert sent.recipient_id == candidate.id
        assert message_client.list_messages(candidate.id) == [sent]
    finally:
        runner.shutdown()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
