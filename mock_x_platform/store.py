from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from models.direct_message import DirectMessage


class MockXStore:
    """SQLite persistence for fake DMs and deterministic failure rules."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    sender_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, id);
                CREATE TABLE IF NOT EXISTS failure_rules (
                    operation TEXT PRIMARY KEY,
                    status INTEGER NOT NULL,
                    detail TEXT NOT NULL,
                    remaining INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    username TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    location TEXT NOT NULL,
                    profile_image_url TEXT NOT NULL,
                    verified INTEGER NOT NULL,
                    receives_your_dm INTEGER NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS profiles_fts USING fts5(
                    id UNINDEXED,
                    name,
                    username,
                    description,
                    location,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                CREATE TABLE IF NOT EXISTS evaluation_cases (
                    id INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    criteria_json TEXT NOT NULL,
                    expected_profile_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dataset_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def profile_count(self) -> int:
        with self._connection() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM profiles").fetchone()
        return int(row["count"])

    def replace_profiles(self, profiles: list[dict[str, Any]]) -> None:
        rows = [
            (
                str(profile["id"]),
                str(profile["name"]),
                str(profile["username"]),
                str(profile.get("description", "")),
                str(profile.get("location", "")),
                str(profile.get("profile_image_url", "")),
                int(bool(profile.get("verified", False))),
                int(bool(profile.get("receives_your_dm", False))),
            )
            for profile in profiles
        ]
        with self._connection() as connection:
            connection.execute("DELETE FROM profiles")
            connection.execute("DELETE FROM profiles_fts")
            connection.executemany(
                """
                INSERT INTO profiles (
                    id, name, username, description, location,
                    profile_image_url, verified, receives_your_dm
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.executemany(
                """
                INSERT INTO profiles_fts (id, name, username, description, location)
                VALUES (?, ?, ?, ?, ?)
                """,
                [row[:5] for row in rows],
            )

    def search_profiles(self, query: str, *, limit: int = 250) -> list[dict[str, Any]]:
        tokens = [token for token in query.split() if token]
        if not tokens:
            return []
        quoted = [f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens]
        bounded_limit = min(max(int(limit), 1), 1000)
        with self._connection() as connection:
            rows = self._fts_rows(connection, " AND ".join(quoted), bounded_limit)
            if len(rows) < min(10, bounded_limit) and len(quoted) > 1:
                fallback = self._fts_rows(connection, " OR ".join(quoted), bounded_limit)
                seen = {str(row["id"]) for row in rows}
                rows.extend(row for row in fallback if str(row["id"]) not in seen)
                rows = rows[:bounded_limit]
        return [_profile_row(row) for row in rows]

    @staticmethod
    def _fts_rows(
        connection: sqlite3.Connection, query: str, limit: int
    ) -> list[sqlite3.Row]:
        return connection.execute(
            """
            SELECT p.*
            FROM profiles_fts
            JOIN profiles AS p ON p.id = profiles_fts.id
            WHERE profiles_fts MATCH ?
            ORDER BY bm25(profiles_fts)
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()

    def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM profiles WHERE id = ?", (str(profile_id),)
            ).fetchone()
        return _profile_row(row) if row is not None else None

    def replace_evaluation_cases(self, cases: list[dict[str, Any]]) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM evaluation_cases")
            connection.executemany(
                """
                INSERT INTO evaluation_cases (
                    id, description, criteria_json, expected_profile_id
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (
                        int(case["id"]),
                        str(case["description"]),
                        str(case["criteria_json"]),
                        str(case["expected_profile_id"]),
                    )
                    for case in cases
                ],
            )

    def evaluation_case_count(self) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM evaluation_cases"
            ).fetchone()
        return int(row["count"])

    def evaluation_cases(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM evaluation_cases ORDER BY id"
        parameters: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (max(int(limit), 0),)
        with self._connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    def replace_dataset_metadata(self, metadata: dict[str, object]) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM dataset_metadata")
            connection.executemany(
                "INSERT INTO dataset_metadata (key, value) VALUES (?, ?)",
                [(str(key), str(value)) for key, value in sorted(metadata.items())],
            )

    def dataset_metadata(self) -> dict[str, str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT key, value FROM dataset_metadata ORDER BY key"
            ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def save_message(
        self,
        *,
        sender_id: str,
        recipient_id: str,
        text: str,
    ) -> DirectMessage:
        conversation_id = conversation_id_for(sender_id, recipient_id)
        created_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO messages (
                    conversation_id, sender_id, recipient_id, text, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, sender_id, recipient_id, text, created_at),
            )
            message_id = str(cursor.lastrowid)
        return DirectMessage(
            id=message_id,
            conversation_id=conversation_id,
            sender_id=sender_id,
            recipient_id=recipient_id,
            text=text,
            created_at=created_at,
        )

    def list_messages(self, *, owner_id: str, participant_id: str) -> list[DirectMessage]:
        conversation_id = conversation_id_for(owner_id, participant_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, conversation_id, sender_id, recipient_id, text, created_at
                FROM messages
                WHERE conversation_id = ?
                ORDER BY id ASC
                """,
                (conversation_id,),
            ).fetchall()
        return [DirectMessage.from_dict(dict(row)) for row in rows]

    def all_messages(self) -> list[DirectMessage]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, conversation_id, sender_id, recipient_id, text, created_at
                FROM messages ORDER BY id ASC
                """
            ).fetchall()
        return [DirectMessage.from_dict(dict(row)) for row in rows]

    def set_failure(
        self,
        operation: str,
        *,
        status: int,
        detail: str,
        count: int = 1,
    ) -> None:
        if count < 1:
            raise ValueError("Failure count must be at least 1.")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO failure_rules (operation, status, detail, remaining)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(operation) DO UPDATE SET
                    status = excluded.status,
                    detail = excluded.detail,
                    remaining = excluded.remaining
                """,
                (operation, status, detail, count),
            )

    def consume_failure(self, operation: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, detail, remaining FROM failure_rules WHERE operation = ?",
                (operation,),
            ).fetchone()
            if row is None:
                return None
            remaining = int(row["remaining"])
            if remaining <= 1:
                connection.execute(
                    "DELETE FROM failure_rules WHERE operation = ?", (operation,)
                )
            else:
                connection.execute(
                    "UPDATE failure_rules SET remaining = ? WHERE operation = ?",
                    (remaining - 1, operation),
                )
            return {"status": int(row["status"]), "detail": str(row["detail"])}

    def reset(self) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM messages")
            connection.execute("DELETE FROM failure_rules")


def conversation_id_for(first_id: str, second_id: str) -> str:
    participants = sorted((str(first_id), str(second_id)))
    return f"mock-dm-{participants[0]}-{participants[1]}"


def _profile_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["verified"] = bool(result["verified"])
    result["receives_your_dm"] = bool(result["receives_your_dm"])
    return result
