from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from smc_ict_4.episode_policy_live.domain import (
    Bar,
    EntryZone,
    LiquidityBoundary,
    TradePlan,
)
from smc_ict_4.episode_policy_live.policy import (
    LiquidityEpisodeCoordinator,
    SymbolEpisodePolicy,
)
from smc_ict_4.episode_policy_live.replay_evidence import (
    build_episode_decision_ledger,
)
from smc_ict_4.episode_policy_live.storage import StateStore


MINUTE = 60_000_000_000


def _bar(symbol: str, minute: int, *, close: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        interval_minutes=1,
        open_time_ns=minute * MINUTE,
        close_time_ns=(minute + 1) * MINUTE,
        open=close,
        high=close + 1.0,
        low=close - 1.0,
        close=close,
        volume=10.0,
        quote_volume=1_000.0,
        taker_buy_quote_volume=500.0,
        trade_count=10,
    )


def _source(symbol: str = "BTCUSDT") -> LiquidityBoundary:
    return LiquidityBoundary(
        boundary_id=f"SOURCE:{symbol}",
        symbol=symbol,
        side="LOW",
        kind="PIVOT_LOW",
        timeframe_minutes=5,
        observed_time_ns=0,
        lower=99.0,
        upper=101.0,
        price=100.0,
        strength=1.0,
    )


def _plan(symbol: str, episode_id: str, minute: int = 2) -> TradePlan:
    return TradePlan(
        episode_id=episode_id,
        plan_id=f"PLAN:{episode_id}",
        symbol=symbol,
        family="FAILED_AUCTION_REVERSAL",
        side="LONG",
        decision_time_ns=minute * MINUTE,
        entry=100.0,
        stop=99.0,
        target=101.0,
        expires_time_ns=(1 << 63) - 1,
        source_boundary_id=f"SOURCE:{symbol}",
        destination_boundary_id=f"DEST:{symbol}",
        entry_zone=EntryZone("SOURCE", 99.5, 100.0, 0, 0),
        evidence={
            "interaction_time_ns": 0,
            "source_kind": "PIVOT_LOW",
            "source_side": "LOW",
            "source_timeframe_minutes": 5,
            "source_observed_time_ns": 0,
            "interaction_source_lower": 99.0,
            "interaction_source_upper": 101.0,
        },
    )


def _persist_events(store: StateStore, events: list[dict[str, object]]) -> None:
    for event in events:
        store.append_event(
            time_ns=int(event["time_ns"]),
            event_type=str(event["event_type"]),
            event_key=str(event["event_key"]),
            payload=event["payload"],  # type: ignore[arg-type]
        )


def test_semantic_event_key_is_idempotent_and_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    with StateStore(tmp_path / "state.sqlite") as store:
        first = store.append_event(
            time_ns=1,
            event_type="POLICY_EPISODE_STARTED",
            event_key="POLICY_EPISODE_STARTED:EP:1",
            payload={"episode_id": "EP:1"},
        )
        repeated = store.append_event(
            time_ns=1,
            event_type="POLICY_EPISODE_STARTED",
            event_key="POLICY_EPISODE_STARTED:EP:1",
            payload={"episode_id": "EP:1"},
        )
        assert repeated == first
        assert store.counts()["runtime_events"] == 1
        with pytest.raises(RuntimeError, match="conflicting runtime event"):
            store.append_event(
                time_ns=2,
                event_type="POLICY_EPISODE_STARTED",
                event_key="POLICY_EPISODE_STARTED:EP:1",
                payload={"episode_id": "EP:1", "changed": True},
            )
        assert store.verify_hash_chain()


def test_state_store_migrates_pre_event_key_runtime_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE runtime_events ("
            "sequence INTEGER PRIMARY KEY AUTOINCREMENT, time_ns INTEGER NOT NULL, "
            "event_type TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL UNIQUE)",
        )
    with StateStore(path) as store:
        columns = {
            str(row["name"])
            for row in store.connection.execute("PRAGMA table_info(runtime_events)")
        }
        assert "event_key" in columns
        store.append_event(
            time_ns=1,
            event_type="POLICY_EPISODE_STARTED",
            event_key="POLICY_EPISODE_STARTED:EP:MIGRATED",
            payload={"episode_id": "EP:MIGRATED"},
        )


def test_incomplete_diagnostics_do_not_emit_terminal_decisions() -> None:
    policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    interaction = _bar("BTCUSDT", 0)
    policy._start_failed(
        _source(),
        "LONG",
        interaction,
        0,
        interaction.low,
        1.0,
        0.0,
    )
    watch = next(iter(policy._watches.values()))
    starts = policy.drain_decision_events()
    assert [item["event_type"] for item in starts] == ["POLICY_EPISODE_STARTED"]

    for minute in (1, 2, 3):
        policy._record(
            "AUCTION_SEQUENCE_INCOMPLETE",
            watch,
            _bar("BTCUSDT", minute),
        )
    assert policy.drain_decision_events() == []

    policy._record_terminal(
        "HISTORY_UNAVAILABLE_TERMINAL",
        watch,
        _bar("BTCUSDT", 4),
    )
    terminal = policy.drain_decision_events()
    assert len(terminal) == 1
    assert terminal[0]["event_type"] == "POLICY_EPISODE_TERMINAL"
    assert terminal[0]["payload"]["outcome"] == "NO_TRADE"  # type: ignore[index]


def test_claim_and_rejection_are_durable_terminal_decisions() -> None:
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    policies = {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols}
    coordinator = LiquidityEpisodeCoordinator(policies)
    selected = _plan("BTCUSDT", "EP:SELECTED")
    rejected = _plan("ETHUSDT", "EP:REJECTED")
    policies["BTCUSDT"]._proposals[selected.episode_id] = selected
    policies["ETHUSDT"]._proposals[rejected.episode_id] = rejected

    coordinator.claim(selected, time_ns=3 * MINUTE)
    coordinator.reject_proposal(
        rejected,
        "MAX_LEVERAGE_EXCEEDED",
        time_ns=3 * MINUTE,
    )
    events = coordinator.drain_decision_events()
    terminals = {
        item["payload"]["episode_id"]: item["payload"]  # type: ignore[index]
        for item in events
        if item["event_type"] == "POLICY_EPISODE_TERMINAL"
    }
    assert terminals["EP:SELECTED"]["outcome"] == "SELECTED"
    assert terminals["EP:SELECTED"]["terminal_reason"] == "ENTRY_ORDER_ACCEPTED"
    assert terminals["EP:REJECTED"]["outcome"] == "NO_TRADE"
    assert terminals["EP:REJECTED"]["terminal_reason"] == "MAX_LEVERAGE_EXCEEDED"

    saved = coordinator.export_state()
    restored = LiquidityEpisodeCoordinator(
        {symbol: SymbolEpisodePolicy(symbol, 0.1) for symbol in symbols},
    )
    restored.restore_state(saved)
    assert restored.export_state() == saved


def test_episode_decisions_join_trades_and_keep_ongoing_separate(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "state.sqlite"
    selected_policy = SymbolEpisodePolicy("BTCUSDT", 0.1)
    selected = _plan("BTCUSDT", "EP:SELECTED")
    selected_policy._proposals[selected.episode_id] = selected
    selected_policy.claim(selected, time_ns=2 * MINUTE)

    ongoing_policy = SymbolEpisodePolicy("ETHUSDT", 0.1)
    interaction = _bar("ETHUSDT", 0)
    ongoing_policy._start_failed(
        _source("ETHUSDT"),
        "LONG",
        interaction,
        0,
        interaction.low,
        1.0,
        0.0,
    )

    with StateStore(state_path) as store:
        for minute in range(3):
            store.append_bar(_bar("BTCUSDT", minute))
            store.append_bar(_bar("ETHUSDT", minute))
        _persist_events(store, selected_policy.drain_decision_events())
        _persist_events(store, ongoing_policy.drain_decision_events())

    rows, metrics = build_episode_decision_ledger(
        state_path,
        trades=[{
            "trade_id": "TRADE:1",
            "episode_id": "EP:SELECTED",
            "plan_id": "PLAN:EP:SELECTED",
            "outcome": "TARGET",
            "entry_time_ns": 2 * MINUTE,
            "exit_time_ns": 3 * MINUTE,
        }],
    )
    by_episode = {item["episode_id"]: item for item in rows}
    assert by_episode["EP:SELECTED"]["episode_status"] == "TERMINAL"
    assert by_episode["EP:SELECTED"]["execution_disposition"] == "FILLED_CLOSED"
    assert by_episode["EP:SELECTED"]["trade_join_status"] == "EXACT_EPISODE_PLAN"
    assert by_episode["EP:SELECTED"]["offline_future_outcome"] == "NOT_EVALUATED"
    ongoing = next(
        item for item in rows if item["episode_status"] == "ONGOING_AT_REPLAY_END"
    )
    assert ongoing["symbol"] == "ETHUSDT"
    assert ongoing["terminal_reason"] is None
    assert ongoing["chart_coverage_status"] == "EXACT"
    assert metrics["started_episodes"] == 2
    assert metrics["terminal_episodes"] == 1
    assert metrics["ongoing_episodes"] == 1
