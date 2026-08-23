from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator, Mapping

from .contracts import EpisodePlan, canonical_json

ZERO_HASH = "0" * 64


class StoreError(RuntimeError):
    pass


class EventStore:
    """Durable cross-process state with a verifiable append-only event chain."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._configure()
        self._migrate()

    def _configure(self) -> None:
        connection = self._connection
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA wal_autocheckpoint=1000")

    def _migrate(self) -> None:
        script = """
        CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL UNIQUE,
            event_time_ns INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE,
            created_at_utc TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            updated_time_ns INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS market_bars (
            stream TEXT NOT NULL,
            symbol TEXT NOT NULL,
            open_time_ms INTEGER NOT NULL,
            close_time_ms INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            PRIMARY KEY (stream, symbol, open_time_ms)
        );
        CREATE INDEX IF NOT EXISTS idx_market_bars_close
            ON market_bars(stream, symbol, close_time_ms);
        CREATE TABLE IF NOT EXISTS decisions (
            decision_id TEXT PRIMARY KEY,
            episode_id TEXT NOT NULL UNIQUE,
            action_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            order_time_ns INTEGER NOT NULL,
            status TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            claimed_by TEXT,
            claimed_time_ns INTEGER,
            terminal_reason TEXT,
            updated_time_ns INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_decisions_status_time
            ON decisions(status, order_time_ns, symbol);
        CREATE TABLE IF NOT EXISTS account_slot (
            slot_id INTEGER PRIMARY KEY CHECK (slot_id = 1),
            decision_id TEXT,
            episode_id TEXT,
            symbol TEXT,
            state TEXT NOT NULL,
            updated_time_ns INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO account_slot
            (slot_id, decision_id, episode_id, symbol, state, updated_time_ns)
        VALUES (1, NULL, NULL, NULL, 'FREE', 0);
        CREATE TABLE IF NOT EXISTS process_leases (
            lease_name TEXT PRIMARY KEY,
            owner_id TEXT NOT NULL,
            heartbeat_time_ns INTEGER NOT NULL,
            expires_time_ns INTEGER NOT NULL
        );
        """
        with self._lock:
            self._connection.executescript(script)

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    @staticmethod
    def now_ns() -> int:
        return time.time_ns()

    def integrity_check(self) -> str:
        row = self._connection.execute("PRAGMA integrity_check").fetchone()
        result = str(row[0]) if row else "missing-result"
        if result.lower() != "ok":
            raise StoreError(f"SQLite integrity check failed: {result}")
        return result

    def append_event(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        event_time_ns: int | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        if not event_type.strip():
            raise ValueError("event_type must be non-empty")
        timestamp = int(event_time_ns or self.now_ns())
        payload_json = canonical_json(payload)
        identity = event_id or hashlib.sha256(
            f"{event_type}|{timestamp}|{payload_json}".encode("utf-8")
        ).hexdigest()
        created = datetime.now(timezone.utc).isoformat()
        with self.transaction(immediate=True) as connection:
            previous = connection.execute(
                "SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = str(previous[0]) if previous else ZERO_HASH
            event_hash = hashlib.sha256(
                f"{previous_hash}|{identity}|{timestamp}|{event_type}|{payload_json}".encode("utf-8")
            ).hexdigest()
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO events(
                        event_id, event_time_ns, event_type, payload_json,
                        previous_hash, event_hash, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (identity, timestamp, event_type, payload_json, previous_hash, event_hash, created),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM events WHERE event_id = ?", (identity,)
                ).fetchone()
                if row is None:
                    raise
                return dict(row)
            sequence = int(cursor.lastrowid)
        return {
            "sequence": sequence,
            "event_id": identity,
            "event_time_ns": timestamp,
            "event_type": event_type,
            "payload_json": payload_json,
            "previous_hash": previous_hash,
            "event_hash": event_hash,
            "created_at_utc": created,
        }

    def verify_event_chain(self) -> dict[str, Any]:
        rows = self._connection.execute(
            "SELECT * FROM events ORDER BY sequence"
        ).fetchall()
        previous_hash = ZERO_HASH
        last_sequence = 0
        for row in rows:
            sequence = int(row["sequence"])
            if sequence <= last_sequence:
                raise StoreError("event sequence is not strictly increasing")
            if str(row["previous_hash"]) != previous_hash:
                raise StoreError(f"event hash chain broke at sequence {sequence}")
            expected = hashlib.sha256(
                (
                    f"{previous_hash}|{row['event_id']}|{row['event_time_ns']}|"
                    f"{row['event_type']}|{row['payload_json']}"
                ).encode("utf-8")
            ).hexdigest()
            if expected != str(row["event_hash"]):
                raise StoreError(f"event hash mismatch at sequence {sequence}")
            previous_hash = expected
            last_sequence = sequence
        return {
            "valid": True,
            "events": len(rows),
            "last_sequence": last_sequence,
            "last_hash": previous_hash,
        }

    def set_checkpoint(self, key: str, payload: Mapping[str, Any]) -> None:
        now = self.now_ns()
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO checkpoints(checkpoint_key, payload_json, updated_time_ns)
                VALUES (?, ?, ?)
                ON CONFLICT(checkpoint_key) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_time_ns=excluded.updated_time_ns
                """,
                (key, canonical_json(payload), now),
            )

    def get_checkpoint(self, key: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT payload_json FROM checkpoints WHERE checkpoint_key = ?", (key,)
        ).fetchone()
        return json.loads(str(row[0])) if row else None

    def put_market_rows(self, stream: str, symbol: str, rows: list[Mapping[str, Any]]) -> int:
        values = []
        for row in rows:
            values.append(
                (
                    stream,
                    symbol,
                    int(row["open_time_ms"]),
                    int(row["close_time_ms"]),
                    canonical_json(row),
                )
            )
        if not values:
            return 0
        with self.transaction(immediate=True) as connection:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT INTO market_bars(stream, symbol, open_time_ms, close_time_ms, payload_json)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(stream, symbol, open_time_ms) DO UPDATE SET
                    close_time_ms=excluded.close_time_ms,
                    payload_json=excluded.payload_json
                """,
                values,
            )
            return int(connection.total_changes - before)

    def market_rows(
        self,
        stream: str,
        symbol: str,
        *,
        start_open_time_ms: int,
        end_close_time_ms: int,
    ) -> list[dict[str, Any]]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM market_bars
            WHERE stream = ? AND symbol = ? AND open_time_ms >= ? AND close_time_ms <= ?
            ORDER BY open_time_ms
            """,
            (stream, symbol, int(start_open_time_ms), int(end_close_time_ms)),
        ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def latest_market_open_ms(self, stream: str, symbol: str) -> int | None:
        row = self._connection.execute(
            "SELECT MAX(open_time_ms) FROM market_bars WHERE stream = ? AND symbol = ?",
            (stream, symbol),
        ).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def enqueue_plan(self, plan: EpisodePlan, *, ready_for_execution: bool) -> bool:
        now = self.now_ns()
        status = "READY" if ready_for_execution else "OBSERVED"
        with self.transaction(immediate=True) as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO decisions(
                        decision_id, episode_id, action_id, symbol, order_time_ns,
                        status, plan_json, updated_time_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.decision_id,
                        plan.episode_id,
                        plan.action_id,
                        plan.symbol,
                        plan.order_time_ns,
                        status,
                        canonical_json(plan.to_dict()),
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        self.append_event(
            "DECISION_READY" if ready_for_execution else "DECISION_OBSERVED",
            plan.to_dict(),
            event_time_ns=plan.order_time_ns,
            event_id=f"decision:{plan.decision_id}",
        )
        return True

    def claim_next_plan(self, consumer_id: str) -> EpisodePlan | None:
        now = self.now_ns()
        with self.transaction(immediate=True) as connection:
            slot = connection.execute(
                "SELECT state FROM account_slot WHERE slot_id = 1"
            ).fetchone()
            if slot is None or str(slot[0]) != "FREE":
                return None
            row = connection.execute(
                """
                SELECT * FROM decisions
                WHERE status = 'READY'
                ORDER BY order_time_ns, symbol, decision_id
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            changed = connection.execute(
                """
                UPDATE decisions SET status='CLAIMED', claimed_by=?, claimed_time_ns=?,
                    updated_time_ns=?
                WHERE decision_id=? AND status='READY'
                """,
                (consumer_id, now, now, row["decision_id"]),
            ).rowcount
            if changed != 1:
                return None
            connection.execute(
                """
                UPDATE account_slot SET decision_id=?, episode_id=?, symbol=?,
                    state='CLAIMED', updated_time_ns=? WHERE slot_id=1 AND state='FREE'
                """,
                (row["decision_id"], row["episode_id"], row["symbol"], now),
            )
            plan = EpisodePlan.from_dict(json.loads(str(row["plan_json"])))
        self.append_event(
            "DECISION_CLAIMED",
            {"decision_id": plan.decision_id, "consumer_id": consumer_id},
            event_id=f"claim:{plan.decision_id}:{consumer_id}",
        )
        return plan

    def mark_submitted(self, decision_id: str, payload: Mapping[str, Any]) -> None:
        now = self.now_ns()
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE decisions SET status='SUBMITTED', updated_time_ns=? WHERE decision_id=?",
                (now, decision_id),
            )
            connection.execute(
                "UPDATE account_slot SET state='ACTIVE', updated_time_ns=? WHERE slot_id=1 AND decision_id=?",
                (now, decision_id),
            )
        self.append_event(
            "ORDER_LIST_SUBMITTED",
            {"decision_id": decision_id, **dict(payload)},
            event_id=f"submitted:{decision_id}",
        )

    def complete_decision(self, decision_id: str, status: str, reason: str, payload: Mapping[str, Any] | None = None) -> None:
        terminal = status.upper()
        if terminal not in {"COMPLETED", "REJECTED", "CANCELED", "EXPIRED", "FAILED"}:
            raise ValueError(f"unsupported terminal status: {status}")
        now = self.now_ns()
        with self.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE decisions SET status=?, terminal_reason=?, updated_time_ns=?
                WHERE decision_id=?
                """,
                (terminal, reason, now, decision_id),
            )
            connection.execute(
                """
                UPDATE account_slot SET decision_id=NULL, episode_id=NULL, symbol=NULL,
                    state='FREE', updated_time_ns=? WHERE slot_id=1 AND decision_id=?
                """,
                (now, decision_id),
            )
        self.append_event(
            f"DECISION_{terminal}",
            {"decision_id": decision_id, "reason": reason, **dict(payload or {})},
            event_id=f"terminal:{decision_id}:{terminal}",
        )

    def account_slot(self) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM account_slot WHERE slot_id=1"
        ).fetchone()
        return dict(row) if row else {}

    def acquire_lease(self, lease_name: str, owner_id: str, ttl_seconds: float) -> bool:
        now = self.now_ns()
        expires = now + int(ttl_seconds * 1_000_000_000)
        with self.transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT owner_id, expires_time_ns FROM process_leases WHERE lease_name=?",
                (lease_name,),
            ).fetchone()
            if row and int(row["expires_time_ns"]) > now and str(row["owner_id"]) != owner_id:
                return False
            connection.execute(
                """
                INSERT INTO process_leases(lease_name, owner_id, heartbeat_time_ns, expires_time_ns)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(lease_name) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    heartbeat_time_ns=excluded.heartbeat_time_ns,
                    expires_time_ns=excluded.expires_time_ns
                """,
                (lease_name, owner_id, now, expires),
            )
        return True

    def heartbeat_lease(self, lease_name: str, owner_id: str, ttl_seconds: float) -> None:
        now = self.now_ns()
        expires = now + int(ttl_seconds * 1_000_000_000)
        with self.transaction(immediate=True) as connection:
            changed = connection.execute(
                """
                UPDATE process_leases SET heartbeat_time_ns=?, expires_time_ns=?
                WHERE lease_name=? AND owner_id=?
                """,
                (now, expires, lease_name, owner_id),
            ).rowcount
            if changed != 1:
                raise StoreError(f"lease lost: {lease_name}")

    def release_lease(self, lease_name: str, owner_id: str) -> None:
        with self.transaction(immediate=True) as connection:
            connection.execute(
                "DELETE FROM process_leases WHERE lease_name=? AND owner_id=?",
                (lease_name, owner_id),
            )

    def status(self) -> dict[str, Any]:
        event_count = int(self._connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        decisions = {
            str(row[0]): int(row[1])
            for row in self._connection.execute(
                "SELECT status, COUNT(*) FROM decisions GROUP BY status ORDER BY status"
            ).fetchall()
        }
        bars = int(self._connection.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0])
        leases = [dict(row) for row in self._connection.execute("SELECT * FROM process_leases ORDER BY lease_name")]
        return {
            "database": str(self.path),
            "integrity": self.integrity_check(),
            "event_chain": self.verify_event_chain(),
            "event_count": event_count,
            "market_bar_rows": bars,
            "decisions": decisions,
            "account_slot": self.account_slot(),
            "leases": leases,
        }

    def close(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._connection.close()

    def __enter__(self) -> "EventStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
