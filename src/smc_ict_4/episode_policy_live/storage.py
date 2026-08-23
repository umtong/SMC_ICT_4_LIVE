"""Durable SQLite state for Windows paper/shadow operation."""
from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Iterator, Mapping

from .domain import Bar, canonical_hash


SCHEMA_VERSION = 2


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._initialize()

    def _initialize(self) -> None:
        # sqlite3 executescript establishes its own transaction boundaries.
        # Keeping it outside the explicit transaction avoids a spurious
        # "no transaction is active" COMMIT on Windows and Linux.
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT NOT NULL,
                interval_minutes INTEGER NOT NULL,
                open_time_ns INTEGER NOT NULL,
                close_time_ns INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                PRIMARY KEY(symbol, interval_minutes, close_time_ns)
            );
            CREATE TABLE IF NOT EXISTS runtime_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                time_ns INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL UNIQUE,
                event_key TEXT
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                name TEXT PRIMARY KEY,
                updated_time_ns INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL
            );
            """
        )
        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(runtime_events)")
        }
        if "event_key" not in columns:
            self.connection.execute(
                "ALTER TABLE runtime_events ADD COLUMN event_key TEXT",
            )
        self.connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS "
            "runtime_events_event_key_unique "
            "ON runtime_events(event_key) WHERE event_key IS NOT NULL",
        )
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def append_bar(self, bar: Bar) -> bool:
        payload = json.dumps(bar.to_dict(), sort_keys=True, separators=(",", ":"))
        digest = sha256(payload.encode()).hexdigest()
        existing = self.connection.execute(
            "SELECT payload_sha256 FROM bars WHERE symbol=? AND interval_minutes=? AND close_time_ns=?",
            (bar.symbol, bar.interval_minutes, bar.close_time_ns),
        ).fetchone()
        if existing is not None:
            if existing["payload_sha256"] != digest:
                raise RuntimeError(
                    f"market data mutation for {bar.symbol} {bar.interval_minutes}m {bar.close_time_ns}"
                )
            return False
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO bars(
                    symbol, interval_minutes, open_time_ns, close_time_ns,
                    payload_json, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    bar.symbol,
                    bar.interval_minutes,
                    bar.open_time_ns,
                    bar.close_time_ns,
                    payload,
                    digest,
                ),
            )
        return True

    def load_bars(
        self,
        *,
        interval_minutes: int,
        since_time_ns: int = 0,
        symbols: Iterable[str] | None = None,
    ) -> list[Bar]:
        clauses = ["interval_minutes=?", "close_time_ns>=?"]
        parameters: list[Any] = [interval_minutes, since_time_ns]
        if symbols is not None:
            values = list(symbols)
            clauses.append(f"symbol IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
        rows = self.connection.execute(
            f"SELECT payload_json FROM bars WHERE {' AND '.join(clauses)} ORDER BY close_time_ns, symbol",
            parameters,
        ).fetchall()
        return [Bar.from_dict(json.loads(row["payload_json"])) for row in rows]

    def append_event(
        self,
        *,
        time_ns: int,
        event_type: str,
        payload: Mapping[str, Any],
        event_key: str | None = None,
    ) -> str:
        """Append one hash-chained runtime event.

        ``event_key`` is optional so existing operational events keep their
        append-only behavior.  A caller which supplies a semantic key gets an
        exactly-once boundary: an identical retry returns the original hash,
        while reusing the key for a different fact fails closed.  The lookup,
        chain-head read and insert share one immediate transaction so a crash
        cannot create two valid rows for the same causal fact.
        """

        if event_key is not None and (not isinstance(event_key, str) or not event_key):
            raise ValueError("event_key must be a non-empty string when supplied")
        payload_json = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
        with self.transaction():
            if event_key is not None:
                existing = self.connection.execute(
                    "SELECT time_ns, event_type, payload_json, event_hash "
                    "FROM runtime_events WHERE event_key=?",
                    (event_key,),
                ).fetchone()
                if existing is not None:
                    if (
                        int(existing["time_ns"]) != int(time_ns)
                        or str(existing["event_type"]) != event_type
                        or str(existing["payload_json"]) != payload_json
                    ):
                        raise RuntimeError(
                            f"conflicting runtime event for semantic key {event_key}",
                        )
                    return str(existing["event_hash"])
            prior = self.connection.execute(
                "SELECT event_hash FROM runtime_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = prior["event_hash"] if prior else "0" * 64
            event_hash = sha256(
                f"{previous_hash}|{time_ns}|{event_type}|{payload_json}".encode("utf-8")
            ).hexdigest()
            self.connection.execute(
                """
                INSERT INTO runtime_events(
                    time_ns, event_type, payload_json, previous_hash, event_hash,
                    event_key
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    time_ns,
                    event_type,
                    payload_json,
                    previous_hash,
                    event_hash,
                    event_key,
                ),
            )
        return event_hash

    def save_snapshot(self, name: str, *, time_ns: int, payload: Mapping[str, Any]) -> str:
        payload_json = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), default=str)
        digest = sha256(payload_json.encode()).hexdigest()
        with self.transaction():
            self.connection.execute(
                """
                INSERT INTO snapshots(name, updated_time_ns, payload_json, payload_sha256)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    updated_time_ns=excluded.updated_time_ns,
                    payload_json=excluded.payload_json,
                    payload_sha256=excluded.payload_sha256
                """,
                (name, time_ns, payload_json, digest),
            )
        return digest

    def load_snapshot(self, name: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT payload_json, payload_sha256 FROM snapshots WHERE name=?", (name,)
        ).fetchone()
        if row is None:
            return None
        digest = sha256(row["payload_json"].encode()).hexdigest()
        if digest != row["payload_sha256"]:
            raise RuntimeError(f"snapshot hash mismatch: {name}")
        return json.loads(row["payload_json"])

    def verify_hash_chain(self) -> bool:
        previous = "0" * 64
        for row in self.connection.execute(
            "SELECT time_ns, event_type, payload_json, previous_hash, event_hash FROM runtime_events ORDER BY sequence"
        ):
            if row["previous_hash"] != previous:
                return False
            expected = sha256(
                f"{previous}|{row['time_ns']}|{row['event_type']}|{row['payload_json']}".encode("utf-8")
            ).hexdigest()
            if expected != row["event_hash"]:
                return False
            previous = row["event_hash"]
        return True

    def integrity_check(self) -> str:
        return str(self.connection.execute("PRAGMA integrity_check").fetchone()[0])

    def counts(self) -> dict[str, int]:
        return {
            "bars": int(self.connection.execute("SELECT COUNT(*) FROM bars").fetchone()[0]),
            "runtime_events": int(self.connection.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]),
            "snapshots": int(self.connection.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]),
        }

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
        finally:
            target.close()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
