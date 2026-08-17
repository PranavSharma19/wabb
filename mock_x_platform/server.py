from __future__ import annotations

import argparse
import json
import logging
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from config import load_settings

from .application import DEFAULT_MAX_RESULTS, MockXApplication, MockXHttpError
from .store import MockXStore


logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1_000_000
DM_SEND_PATTERN = re.compile(r"^/2/dm_conversations/with/([^/]+)/messages$")
DM_LIST_PATTERN = re.compile(r"^/2/dm_conversations/with/([^/]+)/dm_events$")
USER_LOOKUP_PATTERN = re.compile(r"^/2/users/by/username/([^/]+)$")


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    database_path: str | Path | None = None,
) -> ThreadingHTTPServer:
    path = Path(database_path or PROJECT_ROOT / ".cache" / "mock_x_platform.sqlite3")
    application = MockXApplication(MockXStore(path))

    class Handler(MockXRequestHandler):
        app = application

    server = ThreadingHTTPServer((host, int(port)), Handler)
    server.daemon_threads = True
    return server


class MockXRequestHandler(BaseHTTPRequestHandler):
    app: MockXApplication
    server_version = "MockXPlatform/1.0"

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            parsed = urlparse(self.path)
            if method == "GET" and parsed.path == "/health":
                self._json(HTTPStatus.OK, self.app.health())
                return
            if method == "GET" and parsed.path == "/2/users/search":
                query = parse_qs(parsed.query)
                search_text = query.get("query", [""])[0]
                # Absent max_results means X's default of 100, not ours of 10.
                max_results = _as_int(
                    query.get("max_results", [str(DEFAULT_MAX_RESULTS)])[0], "max_results"
                )
                cursor = query.get("next_token", [""])[0] or None
                self._json(
                    HTTPStatus.OK,
                    self.app.search_users(search_text, max_results, cursor),
                )
                return

            lookup_match = USER_LOOKUP_PATTERN.fullmatch(parsed.path)
            if method == "GET" and lookup_match:
                self._json(
                    HTTPStatus.OK,
                    self.app.lookup_user_by_username(unquote(lookup_match.group(1))),
                )
                return

            send_match = DM_SEND_PATTERN.fullmatch(parsed.path)
            if method == "POST" and send_match:
                body = self._read_json()
                self._json(
                    HTTPStatus.CREATED,
                    self.app.send_message(send_match.group(1), str(body.get("text", ""))),
                )
                return

            list_match = DM_LIST_PATTERN.fullmatch(parsed.path)
            if method == "GET" and list_match:
                self._json(HTTPStatus.OK, self.app.list_messages(list_match.group(1)))
                return

            if method == "GET" and parsed.path == "/__mock__/messages":
                self._json(HTTPStatus.OK, self.app.all_messages())
                return
            if method == "POST" and parsed.path == "/__mock__/failures":
                body = self._read_json()
                self._json(
                    HTTPStatus.CREATED,
                    self.app.inject_failure(
                        str(body.get("operation", "")),
                        status=_as_int(body.get("status", 500), "status"),
                        detail=str(body.get("detail", "Injected failure")),
                        count=_as_int(body.get("count", 1), "count"),
                    ),
                )
                return
            if method == "POST" and parsed.path == "/__mock__/reset":
                self._json(HTTPStatus.OK, self.app.reset())
                return
            raise MockXHttpError(404, f"No route for {method} {parsed.path}", "Not found")
        except MockXHttpError as exc:
            self._json(exc.status, exc.to_dict())
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            error = MockXHttpError(400, str(exc), "Invalid request")
            self._json(error.status, error.to_dict())
        except Exception:
            logger.exception("Unhandled Mock X request failure")
            error = MockXHttpError(500, "Internal mock service error.", "Internal error")
            self._json(error.status, error.to_dict())

    def _read_json(self) -> dict[str, Any]:
        length = _as_int(self.headers.get("Content-Length", "0"), "Content-Length")
        if length <= 0:
            return {}
        if length > MAX_REQUEST_BYTES:
            raise MockXHttpError(413, "Request body is too large.", "Payload too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        logger.info("%s - %s", self.client_address[0], format % args)


def _as_int(value: object, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc


def main() -> int:
    settings = load_settings()
    parser = argparse.ArgumentParser(description="Run the local Mock X platform")
    parser.add_argument("--host", default=settings.mock_x_host)
    parser.add_argument("--port", type=int, default=settings.mock_x_port)
    parser.add_argument(
        "--database",
        default=str(settings.mock_x_database_path),
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    server = create_server(args.host, args.port, database_path=args.database)
    address, port = server.server_address[:2]
    print(f"Mock X platform listening at http://{address}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nStopping Mock X platform.")
    finally:
        server.server_close()
    return 0
