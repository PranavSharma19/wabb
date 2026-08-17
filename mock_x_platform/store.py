from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from models.direct_message import DirectMessage

from .pricing import BillingLedger

MATCH_SCOPES = ("name_username", "name_username_bio")
RESULT_ORDERS = ("bm25", "follower_weighted")

# Which FTS table serves each scope. Two tables rather than extra columns on one:
# fts5's bm25 divides by whole-row token length, so folding the bio into the
# default index would re-introduce the length bias round 4 removed, for the
# name_username scope as well as the new one.
_FTS_TABLES = {
    "name_username": "profiles_fts",
    "name_username_bio": "profiles_fts_bio",
}


class MockXStore:
    """SQLite persistence for fake DMs and deterministic failure rules."""

    def __init__(
        self,
        path: str | Path,
        *,
        match_scope: str = "name_username",
        result_order: str = "bm25",
        ledger: BillingLedger | None = None,
    ):
        if match_scope not in MATCH_SCOPES:
            raise ValueError(f"match_scope must be one of: {', '.join(MATCH_SCOPES)}")
        if result_order not in RESULT_ORDERS:
            raise ValueError(f"result_order must be one of: {', '.join(RESULT_ORDERS)}")
        self.path = Path(path)
        self.match_scope = match_scope
        self.result_order = result_order
        # Owned here so every MockXApplication over the same store shares one
        # bill, and so an in-process evaluation can read it without going
        # anywhere near the HTTP layer.
        self.ledger = ledger or BillingLedger()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        if self.result_order == "follower_weighted" and self._follower_counts_are_flat():
            raise ValueError(
                "result_order='follower_weighted' needs a corpus with follower counts, "
                "and this database has none. It predates generator version 3. "
                "Regenerate it with `python -m mock_x_platform.dataset` -- a run against "
                "flat zero counts collapses to id order and would look successful while "
                "measuring nothing."
            )

    def _follower_counts_are_flat(self) -> bool:
        """True when a corpus exists but carries no follower signal at all."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS profiles, MAX(follower_count) AS peak FROM profiles"
            ).fetchone()
        return bool(int(row["profiles"])) and not int(row["peak"] or 0)

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
                    -- NULL means "unknown", which is what X returns when the
                    -- DM eligibility field is absent from a user object.
                    receives_your_dm INTEGER,
                    tier TEXT NOT NULL DEFAULT 'clean',
                    follower_count INTEGER NOT NULL DEFAULT 0
                );
                -- Only the columns X's /2/users/search actually matches. Indexing
                -- the bio and location made document length vary from 3 to 24
                -- tokens, and bm25 divides by length, so sparse profiles floated
                -- to the top of the candidate pool and rich ones fell off the end
                -- of it. Matching X's fields keeps lengths flat and removes that
                -- bias at the source.
                CREATE VIRTUAL TABLE IF NOT EXISTS profiles_fts USING fts5(
                    id UNINDEXED,
                    name,
                    username,
                    tokenize = 'unicode61 remove_diacritics 2'
                );
                -- Used only when match_scope is 'name_username_bio', to model
                -- the possibility that X's query matches bios too (Unknown A).
                CREATE VIRTUAL TABLE IF NOT EXISTS profiles_fts_bio USING fts5(
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
            self._migrate_profiles(connection)
            self._migrate_search_index(connection)
            self._migrate_follower_count(connection)
            self._migrate_bio_index(connection)

    @staticmethod
    def _migrate_profiles(connection: sqlite3.Connection) -> None:
        """Bring a profiles table written by an older build up to date.

        Databases created before dirt tiers lack `tier` and declare
        `receives_your_dm` NOT NULL, which SQLite cannot relax in place. Rebuild
        and copy rather than dropping, so an existing generated dataset survives.
        """

        columns = {
            str(row["name"]): row
            for row in connection.execute("PRAGMA table_info(profiles)").fetchall()
        }
        if not columns:
            return
        stale = "tier" not in columns or bool(columns["receives_your_dm"]["notnull"])
        if not stale:
            return
        connection.executescript(
            """
            CREATE TABLE profiles_migrated (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                username TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL,
                location TEXT NOT NULL,
                profile_image_url TEXT NOT NULL,
                verified INTEGER NOT NULL,
                receives_your_dm INTEGER,
                tier TEXT NOT NULL DEFAULT 'clean'
            );
            INSERT INTO profiles_migrated (
                id, name, username, description, location,
                profile_image_url, verified, receives_your_dm
            )
            SELECT id, name, username, description, location,
                   profile_image_url, verified, receives_your_dm
            FROM profiles;
            DROP TABLE profiles;
            ALTER TABLE profiles_migrated RENAME TO profiles;
            """
        )

    @staticmethod
    def _migrate_search_index(connection: sqlite3.Connection) -> None:
        """Rebuild a search index that still covers description and location."""

        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(profiles_fts)").fetchall()
        }
        if not columns or columns == {"id", "name", "username"}:
            return
        connection.executescript(
            """
            DROP TABLE profiles_fts;
            CREATE VIRTUAL TABLE profiles_fts USING fts5(
                id UNINDEXED,
                name,
                username,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            INSERT INTO profiles_fts (id, name, username)
                SELECT id, name, username FROM profiles;
            """
        )

    @staticmethod
    def _migrate_follower_count(connection: sqlite3.Connection) -> None:
        """Add the follower column to a database generated before it existed.

        This only adds the column at DEFAULT 0; it does not populate it, because
        the counts come from the generator, not from anything the store can
        derive. A corpus migrated this way needs regenerating (generator version
        3+) before result_order='follower_weighted' means anything -- see
        `_follower_counts_are_flat`, which refuses to run that ordering against
        an all-zero corpus instead of silently collapsing to id order.
        """

        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(profiles)").fetchall()
        }
        if not columns or "follower_count" in columns:
            return
        connection.execute(
            "ALTER TABLE profiles ADD COLUMN follower_count INTEGER NOT NULL DEFAULT 0"
        )

    @staticmethod
    def _migrate_bio_index(connection: sqlite3.Connection) -> None:
        """Fill the bio index for a database generated before it existed.

        Without this, switching match_scope on an existing 100k corpus silently
        returns nothing rather than obviously failing.
        """

        profiles = connection.execute("SELECT COUNT(*) AS count FROM profiles").fetchone()
        indexed = connection.execute(
            "SELECT COUNT(*) AS count FROM profiles_fts_bio"
        ).fetchone()
        if not int(profiles["count"]) or int(indexed["count"]):
            return
        connection.execute(
            """
            INSERT INTO profiles_fts_bio (id, name, username, description, location)
            SELECT id, name, username, description, location FROM profiles
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
                _optional_flag(profile.get("receives_your_dm")),
                str(profile.get("tier", "clean")),
                int(profile.get("follower_count", 0) or 0),
            )
            for profile in profiles
        ]
        with self._connection() as connection:
            connection.execute("DELETE FROM profiles")
            connection.execute("DELETE FROM profiles_fts")
            connection.execute("DELETE FROM profiles_fts_bio")
            connection.executemany(
                """
                INSERT INTO profiles (
                    id, name, username, description, location,
                    profile_image_url, verified, receives_your_dm, tier,
                    follower_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            connection.executemany(
                "INSERT INTO profiles_fts (id, name, username) VALUES (?, ?, ?)",
                [row[:3] for row in rows],
            )
            connection.executemany(
                """
                INSERT INTO profiles_fts_bio (id, name, username, description, location)
                VALUES (?, ?, ?, ?, ?)
                """,
                [row[:5] for row in rows],
            )

    def search_profiles(self, query: str, *, limit: int = 250) -> list[dict[str, Any]]:
        """Pool candidates, preferring full-query matches over partial ones.

        Matching every token is a ranking preference, not an admission test: the
        AND rows lead, then the OR rows that AND did not already return. Gating
        the OR pass on "did AND return fewer than ten" made a profile sharing one
        of two name tokens invisible whenever ten other people matched both.
        """

        tokens = [token for token in query.split() if token]
        if not tokens:
            return []
        quoted = [f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens]
        bounded_limit = min(max(int(limit), 1), 1000)
        with self._connection() as connection:
            rows = self._fts_rows(connection, " AND ".join(quoted), bounded_limit)
            if len(quoted) > 1 and len(rows) < bounded_limit:
                fallback = self._fts_rows(connection, " OR ".join(quoted), bounded_limit)
                seen = {str(row["id"]) for row in rows}
                rows.extend(row for row in fallback if str(row["id"]) not in seen)
                rows = rows[:bounded_limit]
        return [_profile_row(row) for row in rows]

    def _fts_rows(
        self, connection: sqlite3.Connection, query: str, limit: int
    ) -> list[sqlite3.Row]:
        # Table and ordering are chosen from validated constants, never from
        # caller input, which is what makes the interpolation below safe.
        table = _FTS_TABLES[self.match_scope]
        # id breaks ties so the pool is byte-identical between runs.
        order = (
            f"bm25({table}), p.id"
            if self.result_order == "bm25"
            else "p.follower_count DESC, p.id"
        )
        return connection.execute(
            f"""
            SELECT p.*
            FROM {table}
            JOIN profiles AS p ON p.id = {table}.id
            WHERE {table} MATCH ?
            ORDER BY {order}
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

    def get_profile_by_username(self, username: str) -> dict[str, Any] | None:
        """Resolve one profile by handle. Case-insensitive: X handles are."""

        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM profiles WHERE username = ? COLLATE NOCASE",
                (str(username or "").lstrip("@"),),
            ).fetchone()
        return _profile_row(row) if row is not None else None

    def profile_labels(self, ids: Iterable[str]) -> dict[str, dict[str, str]]:
        """Return `{id: {tier, name, username}}` for offline evaluation joins.

        Tier is ground truth about how dirty a profile is, so it is deliberately
        kept out of `_profile_row` and never reaches an API response.
        """

        wanted = [str(identifier) for identifier in ids]
        labels: dict[str, dict[str, str]] = {}
        if not wanted:
            return labels
        with self._connection() as connection:
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = connection.execute(
                    f"SELECT id, tier, name, username FROM profiles WHERE id IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    labels[str(row["id"])] = {
                        "tier": str(row["tier"]),
                        "name": str(row["name"]),
                        "username": str(row["username"]),
                    }
        return labels

    def tier_counts(self) -> dict[str, int]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT tier, COUNT(*) AS count FROM profiles GROUP BY tier ORDER BY tier"
            ).fetchall()
        return {str(row["tier"]): int(row["count"]) for row in rows}

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


def _optional_flag(value: Any) -> int | None:
    """Preserve unknown DM eligibility as NULL instead of collapsing it to False."""

    return None if value is None else int(bool(value))


def _profile_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result.pop("tier", None)
    # Same reason as tier: a store-internal signal that must not reach an API
    # response. follower_count models an ordering hypothesis about the real
    # endpoint -- code under evaluation must not be able to read it directly.
    result.pop("follower_count", None)
    result["verified"] = bool(result["verified"])
    dm = result.get("receives_your_dm")
    result["receives_your_dm"] = None if dm is None else bool(dm)
    return result
