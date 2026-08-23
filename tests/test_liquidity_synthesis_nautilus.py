from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from nautilus_trader.model.identifiers import ClientOrderId
from smc_ict_4.episode_policy_live.domain import Bar, EntryZone, SYMBOLS, TradePlan
from smc_ict_4.episode_policy_live.nautilus_backtest import run_native_backtest
from smc_ict_4.episode_policy_live.replay_evidence import build_closed_trade_ledger


MINUTE = 60_000_000_000
BASE = {
    "BTCUSDT": 100.0,
    "ETHUSDT": 200.0,
    "SOLUSDT": 20.0,
    "XRPUSDT": 1.0,
}


def _bar(symbol: str, minute: int, *, open_: float, high: float, low: float, close: float) -> Bar:
    return Bar(
        symbol=symbol,
        interval_minutes=1,
        open_time_ns=minute * MINUTE,
        close_time_ns=(minute + 1) * MINUTE - 1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1_000.0,
        quote_volume=1_000.0 * close,
        taker_buy_quote_volume=500.0 * close,
        trade_count=100,
    )


def _plan(
    symbol: str,
    suffix: str,
    decision_ns: int,
    *,
    entry: float | None = None,
    expires_time_ns: int = 10 * MINUTE,
    entry_event: str | None = None,
) -> TradePlan:
    entry = BASE[symbol] if entry is None else entry
    return TradePlan(
        episode_id=f"EP:{suffix}",
        plan_id=f"PLAN:{suffix}",
        symbol=symbol,
        family="FAILED_AUCTION_REVERSAL",
        side="LONG",
        decision_time_ns=decision_ns,
        entry=entry,
        stop=entry * 0.98,
        target=entry * 1.03,
        expires_time_ns=expires_time_ns,
        source_boundary_id=f"SRC:{suffix}",
        destination_boundary_id=f"DST:{suffix}",
        entry_zone=EntryZone(
            kind="SOURCE_BOUNDARY_RETEST",
            lower=entry * 0.995,
            upper=entry * 1.005,
            observed_time_ns=decision_ns,
            source_bar_open_time_ns=0,
        ),
        evidence={
            "decision_quality": 1.0,
            **({"entry_event": entry_event} if entry_event is not None else {}),
        },
    )


class _TwoPlanCoordinator:
    """Emit competing BTC and ETH plans at one synchronized close."""

    def __init__(self) -> None:
        self.pending: dict[int, set[str]] = {}
        self.emitted = False
        self.claimed: list[str] = []
        self.rejected: list[tuple[str, str]] = []

    def push_bar(self, bar: Bar) -> list[TradePlan]:
        symbols = self.pending.setdefault(bar.close_time_ns, set())
        symbols.add(bar.symbol)
        if self.emitted or symbols != set(SYMBOLS):
            return []
        self.emitted = True
        return [
            _plan("BTCUSDT", "btc", bar.close_time_ns),
            _plan("ETHUSDT", "eth", bar.close_time_ns),
        ]

    def claim(self, plan: TradePlan) -> None:
        self.claimed.append(plan.plan_id)

    def reject_proposal(self, plan: TradePlan, reason: str) -> None:
        self.rejected.append((plan.plan_id, reason))

    def export_state(self) -> dict[str, object]:
        return {
            "version": 1,
            "claimed": list(self.claimed),
            "rejected": list(self.rejected),
        }


class _CompactTwoPlanCoordinator(_TwoPlanCoordinator):
    def export_runtime_state(self) -> dict[str, object]:
        return {
            "version": 2,
            "claimed": list(self.claimed),
            "rejected": list(self.rejected),
        }


class _SolPartialCoordinator(_TwoPlanCoordinator):
    def push_bar(self, bar: Bar) -> list[TradePlan]:
        symbols = self.pending.setdefault(bar.close_time_ns, set())
        symbols.add(bar.symbol)
        if self.emitted or symbols != set(SYMBOLS):
            return []
        self.emitted = True
        return [_plan("SOLUSDT", "sol-partial", bar.close_time_ns)]


class _ClaimValidityPolicy:
    def __init__(self) -> None:
        self.invalid_reason: str | None = None

    def claimed_plan_validity(self, plan_id: str) -> tuple[bool, str | None]:
        assert plan_id == "PLAN:validity"
        return self.invalid_reason is None, self.invalid_reason


class _InvalidatingCoordinator:
    """Expose the production policy validity route to the native adapter."""

    def __init__(self, plan_factory) -> None:
        self.pending: dict[int, set[str]] = {}
        self.completed_groups = 0
        self.emitted = False
        self.claimed: list[str] = []
        self.validity = _ClaimValidityPolicy()
        self.policies = {symbol: self.validity for symbol in SYMBOLS}
        self.plan_factory = plan_factory

    def push_bar(self, bar: Bar) -> list[TradePlan]:
        symbols = self.pending.setdefault(bar.close_time_ns, set())
        symbols.add(bar.symbol)
        if symbols != set(SYMBOLS):
            return []
        self.completed_groups += 1
        if not self.emitted:
            self.emitted = True
            return [self.plan_factory(bar.close_time_ns)]
        if self.completed_groups == 3:
            self.validity.invalid_reason = "OPPOSITE_LEG_CHANGE_OF_CONTROL"
        return []

    def claim(self, plan: TradePlan) -> None:
        self.claimed.append(plan.plan_id)

    def export_state(self) -> dict[str, object]:
        return {
            "version": 1,
            "claimed": list(self.claimed),
            "invalid_reason": self.validity.invalid_reason,
        }


class _SinglePlanCoordinator:
    def __init__(self, plan_factory) -> None:
        self.pending: dict[int, set[str]] = {}
        self.emitted = False
        self.claimed: list[str] = []
        self.rejected: list[tuple[str, str]] = []
        self.plan_factory = plan_factory

    def push_bar(self, bar: Bar) -> list[TradePlan]:
        symbols = self.pending.setdefault(bar.close_time_ns, set())
        symbols.add(bar.symbol)
        if self.emitted or symbols != set(SYMBOLS):
            return []
        self.emitted = True
        return [self.plan_factory(bar.close_time_ns)]

    def claim(self, plan: TradePlan) -> None:
        if plan.plan_id not in self.claimed:
            self.claimed.append(plan.plan_id)

    def reject_proposal(self, plan: TradePlan, reason: str) -> None:
        self.rejected.append((plan.plan_id, reason))

    def export_state(self) -> dict[str, object]:
        return {
            "version": 1,
            "claimed": list(self.claimed),
            "rejected": list(self.rejected),
        }


def _bars() -> list[Bar]:
    output: list[Bar] = []
    for minute in range(6):
        for symbol in SYMBOLS:
            base = BASE[symbol]
            if symbol == "BTCUSDT" and minute == 1:
                output.append(_bar(symbol, minute, open_=101.0, high=101.5, low=99.5, close=100.5))
            elif symbol == "BTCUSDT" and minute >= 2:
                output.append(_bar(symbol, minute, open_=100.5, high=104.0, low=99.5, close=103.0))
            else:
                output.append(
                    _bar(
                        symbol,
                        minute,
                        open_=base * 1.01,
                        high=base * 1.015,
                        low=base * 1.005,
                        close=base * 1.01,
                    )
                )
    return output


def _partial_fill_bars() -> list[Bar]:
    output: list[Bar] = []
    sol = (
        (20.2, 20.3, 20.1, 20.2),
        # Entry and stop are both touched.  Bar-path ambiguity must never
        # receive a favorable same-bar target; every fill chunk exits natively.
        (20.1, 21.0, 19.4, 20.0),
        (20.0, 20.1, 19.4, 19.5),
        (19.5, 19.6, 19.3, 19.4),
    )
    for minute in range(len(sol)):
        for symbol in SYMBOLS:
            if symbol == "SOLUSDT":
                open_, high, low, close = sol[minute]
                output.append(_bar(symbol, minute, open_=open_, high=high, low=low, close=close))
            else:
                base = BASE[symbol]
                output.append(
                    _bar(
                        symbol,
                        minute,
                        open_=base * 1.01,
                        high=base * 1.015,
                        low=base * 1.005,
                        close=base * 1.01,
                    ),
                )
    return output


def _validity_bars(*, fill_entry: bool) -> list[Bar]:
    output: list[Bar] = []
    for minute in range(5):
        for symbol in SYMBOLS:
            base = BASE[symbol]
            if symbol == "BTCUSDT" and fill_entry and minute == 1:
                output.append(
                    _bar(symbol, minute, open_=101.0, high=101.5, low=99.5, close=100.5),
                )
            elif symbol == "BTCUSDT" and fill_entry and minute >= 2:
                output.append(
                    _bar(symbol, minute, open_=100.5, high=101.5, low=99.5, close=100.5),
                )
            else:
                output.append(
                    _bar(
                        symbol,
                        minute,
                        open_=base * 1.01,
                        high=base * 1.015,
                        low=base * 1.005,
                        close=base * 1.01,
                    ),
                )
    return output


def _runtime_events(state_path: Path) -> list[tuple[str, dict[str, object]]]:
    with sqlite3.connect(state_path) as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM runtime_events ORDER BY sequence",
        ).fetchall()
    return [(str(event_type), json.loads(payload)) for event_type, payload in rows]


def test_policy_supersession_cancels_only_still_pending_native_entry(tmp_path: Path) -> None:
    state_path = tmp_path / "policy-invalidates-pending.sqlite3"
    coordinator = _InvalidatingCoordinator(
        lambda decision_ns: _plan(
            "BTCUSDT",
            "validity",
            decision_ns,
            expires_time_ns=10 * MINUTE,
        ),
    )
    result = run_native_backtest(
        _validity_bars(fill_entry=False),
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    events = _runtime_events(state_path)
    parent = next(payload for kind, payload in events if kind == "PARENT_LIMIT_SUBMITTED")
    cancellations = [payload for kind, payload in events if kind == "PENDING_PLAN_CANCELED"]
    assert parent["time_in_force"] == "GTC"
    assert cancellations == [
        {
            "plan_id": "PLAN:validity",
            "policy_reason": "OPPOSITE_LEG_CHANGE_OF_CONTROL",
            "reason": "POLICY_INVALIDATED",
        },
    ]
    assert coordinator.claimed == ["PLAN:validity"]
    assert result.fills.empty
    assert not any(kind == "POSITION_CLOSED" for kind, _ in events)


def test_policy_invalidation_and_plan_expiry_never_time_exit_a_fill(tmp_path: Path) -> None:
    state_path = tmp_path / "policy-invalidates-filled.sqlite3"
    coordinator = _InvalidatingCoordinator(
        lambda decision_ns: _plan(
            "BTCUSDT",
            "validity",
            decision_ns,
            # The deadline passes after the entry fill and at the same group
            # which invalidates the claimed plan.
            expires_time_ns=2 * MINUTE,
        ),
    )
    result = run_native_backtest(
        _validity_bars(fill_entry=True),
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    events = _runtime_events(state_path)
    assert coordinator.validity.invalid_reason == "OPPOSITE_LEG_CHANGE_OF_CONTROL"
    assert not any(kind == "PENDING_PLAN_CANCELED" for kind, _ in events)
    assert not any(kind == "POSITION_CLOSED" for kind, _ in events)
    assert set(result.positions["side"].astype(str)) == {"LONG"}
    parent = next(payload for kind, payload in events if kind == "PARENT_LIMIT_SUBMITTED")
    assert parent["time_in_force"] == "GTC"


def test_native_engine_owns_fills_account_and_one_global_slot(tmp_path: Path) -> None:
    state_path = tmp_path / "native-state.sqlite3"
    coordinator = _TwoPlanCoordinator()
    result = run_native_backtest(
        _bars(),
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    # Exactly one parent enters even though two instruments signal together.
    assert result.parent_orders_submitted == 1
    assert result.plans_blocked_by_global_slot == 1
    assert result.max_active_instruments == 1
    assert result.missing_flow_bars == 0
    assert coordinator.claimed == ["PLAN:btc"]
    assert coordinator.rejected == [("PLAN:eth", "GLOBAL_ACCOUNT_SLOT_BUSY")]

    # The parent can fill in multiple chunks before its cancellation reaches
    # the matcher.  Each actual chunk owns one independent protective pair.
    assert len(result.positions.index) == 1
    assert set(result.fills["instrument_id"].astype(str)) == {"BTCUSDT-PERP.BINANCE"}
    assert result.final_balance != 100_000.0
    with sqlite3.connect(state_path) as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM runtime_events ORDER BY sequence",
        ).fetchall()
        timed_rows = connection.execute(
            "SELECT time_ns, event_type, payload_json FROM runtime_events ORDER BY sequence",
        ).fetchall()
    parent_id = next(
        json.loads(payload)["client_order_id"]
        for event_type, payload in rows
        if event_type == "PARENT_LIMIT_SUBMITTED"
    )
    parent_fill_count = sum(
        event_type == "ORDER_FILLED" and json.loads(payload)["client_order_id"] == parent_id
        for event_type, payload in rows
    )
    event_types = {str(event_type) for event_type, _ in rows}
    assert parent_fill_count >= 1
    assert result.protective_pairs_submitted == parent_fill_count
    assert len(result.fills.index) == 1 + parent_fill_count
    assert "PARENT_LIMIT_SUBMITTED" in event_types
    assert "PROTECTION_SUBMITTED" in event_types
    assert "BRACKET_SUBMITTED" not in event_types
    assert "EXECUTION_HALT" not in event_types
    # Deferred targets become eligible only after the entry bar.  A sibling
    # stop remains live across partial target fills and is canceled only after
    # that target order has completed.
    indexed = list(enumerate(rows))
    activated_target_ids = {
        json.loads(payload)["target_client_order_id"]
        for event_type, payload in rows
        if event_type == "DEFERRED_TARGET_ACTIVATED"
    }
    parent_fill_times = [
        time_ns
        for time_ns, event_type, payload in timed_rows
        if event_type == "ORDER_FILLED"
        and json.loads(payload)["client_order_id"] == parent_id
    ]
    target_fill_times = [
        time_ns
        for time_ns, event_type, payload in timed_rows
        if event_type == "ORDER_FILLED"
        and json.loads(payload)["client_order_id"] in activated_target_ids
    ]
    assert parent_fill_times and target_fill_times
    assert min(target_fill_times) > max(parent_fill_times)
    for _, payload in (
        (index, json.loads(payload))
        for index, (event_type, payload) in indexed
        if event_type == "DEFERRED_TARGET_ACTIVATED"
    ):
        stop_id = payload["stop_client_order_id"]
        target_id = payload["target_client_order_id"]
        target_fills = [
            index
            for index, (event_type, raw) in indexed
            if event_type == "ORDER_FILLED"
            and json.loads(raw)["client_order_id"] == target_id
        ]
        stop_cancels = [
            index
            for index, (event_type, raw) in indexed
            if event_type == "ORDER_CANCELED"
            and json.loads(raw)["client_order_id"] == stop_id
        ]
        assert target_fills
        assert stop_cancels
        assert max(target_fills) < min(stop_cancels)
    assert set(result.positions["side"].astype(str)) == {"FLAT"}


def test_compact_runtime_snapshot_does_not_change_native_execution_results(
    tmp_path: Path,
) -> None:
    baseline_state = tmp_path / "baseline-full-runtime.sqlite3"
    compact_state = tmp_path / "compact-runtime.sqlite3"
    baseline_coordinator = _TwoPlanCoordinator()
    compact_coordinator = _CompactTwoPlanCoordinator()
    baseline = run_native_backtest(
        _bars(),
        state_path=baseline_state,
        configure_strategy=lambda strategy: setattr(
            strategy, "coordinator", baseline_coordinator,
        ),
    )
    compact = run_native_backtest(
        _bars(),
        state_path=compact_state,
        configure_strategy=lambda strategy: setattr(
            strategy, "coordinator", compact_coordinator,
        ),
    )

    assert compact_coordinator.claimed == baseline_coordinator.claimed
    assert compact_coordinator.rejected == baseline_coordinator.rejected
    assert (
        compact.final_balance,
        compact.final_nav,
        compact.parent_orders_submitted,
        compact.protective_pairs_submitted,
        compact.plans_blocked_by_global_slot,
        compact.max_active_instruments,
    ) == (
        baseline.final_balance,
        baseline.final_nav,
        baseline.parent_orders_submitted,
        baseline.protective_pairs_submitted,
        baseline.plans_blocked_by_global_slot,
        baseline.max_active_instruments,
    )

    # Nautilus creates fresh UUIDs for init_id and position_id on each engine
    # construction. Every deterministic order/fill/account field is exact.
    baseline_fills = baseline.fills.drop(columns=["init_id"]).reset_index(drop=True)
    compact_fills = compact.fills.drop(columns=["init_id"]).reset_index(drop=True)
    assert baseline_fills.equals(compact_fills)
    baseline_positions = baseline.positions.reset_index().drop(columns=["position_id"])
    compact_positions = compact.positions.reset_index().drop(columns=["position_id"])
    assert baseline_positions.equals(compact_positions)
    assert baseline.account.reset_index(drop=True).equals(
        compact.account.reset_index(drop=True),
    )

    baseline_trades, _ = build_closed_trade_ledger(
        baseline.positions,
        baseline.fills,
        state_path=baseline_state,
    )
    compact_trades, _ = build_closed_trade_ledger(
        compact.positions,
        compact.fills,
        state_path=compact_state,
    )
    for trade in (*baseline_trades, *compact_trades):
        trade.pop("trade_id", None)
        trade.pop("position_id", None)
    assert compact_trades == baseline_trades


def test_policy_semantic_events_persist_on_bar_and_execution_admission(
    tmp_path: Path,
) -> None:
    class LedgerCoordinator(_SinglePlanCoordinator):
        def __init__(self) -> None:
            super().__init__(lambda decision_ns: _plan("BTCUSDT", "ledger", decision_ns))
            self.pending_events: list[dict[str, object]] = []

        def push_bar(self, bar: Bar) -> list[TradePlan]:
            plans = super().push_bar(bar)
            if plans:
                plan = plans[0]
                self.pending_events.append(
                    {
                        "time_ns": plan.decision_time_ns,
                        "event_type": "POLICY_EPISODE_STARTED",
                        "event_key": f"POLICY_EPISODE_STARTED:{plan.episode_id}",
                        "payload": {
                            "episode_id": plan.episode_id,
                            "started_time_ns": plan.decision_time_ns,
                        },
                    },
                )
            return plans

        def claim(self, plan: TradePlan, *, time_ns: int | None = None) -> None:
            super().claim(plan)
            assert time_ns is not None
            self.pending_events.append(
                {
                    "time_ns": time_ns,
                    "event_type": "POLICY_EPISODE_TERMINAL",
                    "event_key": f"POLICY_EPISODE_TERMINAL:{plan.episode_id}",
                    "payload": {
                        "episode_id": plan.episode_id,
                        "outcome": "SELECTED",
                        "stage": "EXECUTION_ADMISSION",
                        "terminal_time_ns": time_ns,
                    },
                },
            )

        def reject_proposal(
            self,
            plan: TradePlan,
            reason: str,
            *,
            time_ns: int | None = None,
        ) -> None:
            super().reject_proposal(plan, reason)

        def drain_decision_events(self) -> list[dict[str, object]]:
            events = list(self.pending_events)
            self.pending_events.clear()
            return events

    coordinator = LedgerCoordinator()
    state_path = tmp_path / "semantic-policy-ledger.sqlite3"
    run_native_backtest(
        _bars(),
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    with sqlite3.connect(state_path) as connection:
        rows = connection.execute(
            "SELECT sequence, time_ns, event_type, event_key, payload_json "
            "FROM runtime_events ORDER BY sequence",
        ).fetchall()
    started = next(row for row in rows if row[2] == "POLICY_EPISODE_STARTED")
    terminal = next(row for row in rows if row[2] == "POLICY_EPISODE_TERMINAL")
    parent = next(row for row in rows if row[2] == "PARENT_ORDER_SUBMITTED")
    claimed = next(row for row in rows if row[2] == "EPISODE_CLAIMED")

    assert started[3] == "POLICY_EPISODE_STARTED:EP:ledger"
    assert started[0] < parent[0]
    assert terminal[3] == "POLICY_EPISODE_TERMINAL:EP:ledger"
    assert json.loads(terminal[4])["stage"] == "EXECUTION_ADMISSION"
    assert terminal[1] == claimed[1]
    assert terminal[0] < claimed[0]


def test_every_parent_fill_chunk_gets_its_own_protection_pair(tmp_path: Path) -> None:
    state_path = tmp_path / "partial-fill-state.sqlite3"
    coordinator = _SolPartialCoordinator()
    result = run_native_backtest(
        _partial_fill_bars(),
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    with sqlite3.connect(state_path) as connection:
        rows = connection.execute(
            "SELECT event_type, payload_json FROM runtime_events ORDER BY sequence",
        ).fetchall()
    parent_id = next(
        json.loads(payload)["client_order_id"]
        for event_type, payload in rows
        if event_type == "PARENT_LIMIT_SUBMITTED"
    )
    parent_fills = [
        payload
        for event_type, payload in rows
        if event_type == "ORDER_FILLED"
        and json.loads(payload)["client_order_id"] == parent_id
    ]
    event_types = [event_type for event_type, _ in rows]
    protection_payloads = [
        json.loads(payload)
        for event_type, payload in rows
        if event_type == "PROTECTION_SUBMITTED"
    ]

    assert len(parent_fills) >= 2  # cancellation raced with same-bar matching
    assert result.protective_pairs_submitted == len(parent_fills)
    assert "EXECUTION_HALT" not in event_types
    assert "ORDER_REJECTED" not in event_types
    assert "DEFERRED_TARGET_ACTIVATED" not in event_types
    assert all(
        item.get("reason") == "FILL_BAR_STOP_OR_AMBIGUOUS_NATIVE_MARKET_EXIT"
        for item in protection_payloads
    )
    assert coordinator.claimed == ["PLAN:sol-partial"]
    # The native market exit itself occurs near the bar close.  The account and
    # native Position evidence must nevertheless own the declared adverse stop,
    # not the old favorable near-flat result.
    assert result.conservative_stop_adjustments
    assert sum(item.cash_delta for item in result.conservative_stop_adjustments) < -2_500
    assert result.final_balance < 97_500
    assert float(str(result.positions.iloc[0]["realized_pnl"]).split()[0]) < -2_500


def test_busy_slot_terminalizes_immediate_acceptance_as_missed(tmp_path: Path) -> None:
    class CompetingCoordinator(_TwoPlanCoordinator):
        def push_bar(self, bar: Bar) -> list[TradePlan]:
            symbols = self.pending.setdefault(bar.close_time_ns, set())
            symbols.add(bar.symbol)
            if self.emitted or symbols != set(SYMBOLS):
                return []
            self.emitted = True
            return [
                _plan("BTCUSDT", "owner", bar.close_time_ns),
                _plan(
                    "SOLUSDT",
                    "accepted-missed",
                    bar.close_time_ns,
                    entry=20.2,
                    entry_event="ACCEPTANCE_FIRST_RESPONSE_CLOSE",
                ),
            ]

    coordinator = CompetingCoordinator()
    state_path = tmp_path / "busy-acceptance-missed.sqlite3"
    result = run_native_backtest(
        _bars(),
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    assert result.parent_orders_submitted == 1
    assert coordinator.rejected == [
        ("PLAN:accepted-missed", "GLOBAL_SLOT_BUSY_IMMEDIATE_RESPONSE_MISSED"),
    ]
    events = _runtime_events(state_path)
    assert any(
        kind == "IMMEDIATE_RESPONSE_MISSED_GLOBAL_SLOT"
        and payload["plan_id"] == "PLAN:accepted-missed"
        for kind, payload in events
    )


def test_busy_slot_preserves_untouched_failed_first_return_until_release(
    tmp_path: Path,
) -> None:
    class CompetingCoordinator(_TwoPlanCoordinator):
        def push_bar(self, bar: Bar) -> list[TradePlan]:
            symbols = self.pending.setdefault(bar.close_time_ns, set())
            symbols.add(bar.symbol)
            if self.emitted or symbols != set(SYMBOLS):
                return []
            self.emitted = True
            return [
                _plan("BTCUSDT", "slot-owner", bar.close_time_ns),
                _plan(
                    "SOLUSDT",
                    "wait-first-return",
                    bar.close_time_ns,
                    entry=20.0,
                    entry_event="FAILED_AUCTION_FUTURE_FIRST_RETURN",
                ),
            ]

    bars: list[Bar] = []
    for minute in range(6):
        for symbol in SYMBOLS:
            if symbol == "BTCUSDT":
                if minute == 0:
                    values = (101.0, 101.5, 100.5, 101.0)
                elif minute == 1:
                    values = (100.5, 101.0, 99.5, 100.5)
                else:
                    values = (100.5, 104.0, 100.2, 103.0)
            elif symbol == "SOLUSDT":
                values = (
                    (20.2, 20.3, 20.1, 20.2)
                    if minute < 5
                    else (20.1, 20.2, 19.9, 20.0)
                )
            else:
                base = BASE[symbol]
                values = (base * 1.01, base * 1.015, base * 1.005, base * 1.01)
            bars.append(
                _bar(
                    symbol,
                    minute,
                    open_=values[0],
                    high=values[1],
                    low=values[2],
                    close=values[3],
                ),
            )

    coordinator = CompetingCoordinator()
    state_path = tmp_path / "busy-failed-waits.sqlite3"
    result = run_native_backtest(
        bars,
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    assert result.parent_orders_submitted == 2
    assert coordinator.rejected == []
    assert coordinator.claimed == ["PLAN:slot-owner", "PLAN:wait-first-return"]
    events = _runtime_events(state_path)
    wait_index = next(
        index for index, (kind, _payload) in enumerate(events)
        if kind == "PLAN_WAITING_GLOBAL_SLOT"
    )
    release_index = next(
        index for index, (kind, _payload) in enumerate(events)
        if kind == "WAITING_GLOBAL_SLOT_RELEASED"
    )
    second_submit = next(
        index for index, (kind, payload) in enumerate(events)
        if kind == "PARENT_ORDER_SUBMITTED"
        and payload["plan"]["plan_id"] == "PLAN:wait-first-return"
    )
    assert wait_index < release_index < second_submit


def test_slot_release_reranks_waiting_with_fresh_instead_of_fifo(
    tmp_path: Path,
) -> None:
    class RerankCoordinator(_TwoPlanCoordinator):
        def __init__(self) -> None:
            super().__init__()
            self.completed_groups = 0
            self.arbitrated: list[tuple[str, ...]] = []

        @staticmethod
        def ranked_plan(
            symbol: str,
            suffix: str,
            decision_ns: int,
            rank: float,
        ) -> TradePlan:
            plan = _plan(
                symbol,
                suffix,
                decision_ns,
                entry_event="FAILED_AUCTION_FUTURE_FIRST_RETURN",
            )
            return TradePlan.from_dict(
                {
                    **plan.to_dict(),
                    "evidence": {
                        **plan.evidence,
                        "event_local_progress": 0.01,
                        "absolute_delivery_per_risk": rank,
                    },
                },
            )

        def push_bar(self, bar: Bar) -> list[TradePlan]:
            symbols = self.pending.setdefault(bar.close_time_ns, set())
            symbols.add(bar.symbol)
            if symbols != set(SYMBOLS):
                return []
            self.completed_groups += 1
            if self.completed_groups == 1:
                return [
                    _plan("BTCUSDT", "rerank-owner", bar.close_time_ns),
                    self.ranked_plan(
                        "SOLUSDT",
                        "older-waiting",
                        bar.close_time_ns,
                        1.0,
                    ),
                ]
            if self.completed_groups == 4:
                return [
                    self.ranked_plan(
                        "ETHUSDT",
                        "fresh-better",
                        bar.close_time_ns,
                        2.0,
                    ),
                ]
            return []

        def arbitrate(self, candidates: tuple[TradePlan, ...]) -> list[TradePlan]:
            self.arbitrated.append(tuple(item.plan_id for item in candidates))
            return [
                max(
                    candidates,
                    key=lambda item: float(
                        item.evidence.get("absolute_delivery_per_risk", 0.0),
                    ),
                ),
            ]

    bars: list[Bar] = []
    for minute in range(9):
        for symbol in SYMBOLS:
            if symbol == "BTCUSDT":
                values = (
                    (101.0, 101.5, 100.5, 101.0)
                    if minute == 0
                    else (100.5, 101.0, 99.5, 100.5)
                    if minute == 1
                    else (100.5, 104.0, 100.2, 103.0)
                )
            elif symbol == "ETHUSDT":
                values = (
                    (202.0, 203.0, 201.0, 202.0)
                    if minute < 4
                    else (202.0, 203.0, 199.9, 201.0)
                    if minute == 4
                    else (201.0, 207.0, 200.5, 206.0)
                )
            elif symbol == "SOLUSDT":
                values = (
                    (20.2, 20.3, 20.1, 20.2)
                    if minute < 7
                    else (20.1, 20.2, 19.9, 20.0)
                )
            else:
                base = BASE[symbol]
                values = (base * 1.01, base * 1.015, base * 1.005, base * 1.01)
            bars.append(
                _bar(
                    symbol,
                    minute,
                    open_=values[0],
                    high=values[1],
                    low=values[2],
                    close=values[3],
                ),
            )

    coordinator = RerankCoordinator()
    state_path = tmp_path / "slot-release-rerank.sqlite3"
    result = run_native_backtest(
        bars,
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    assert result.parent_orders_submitted == 3
    assert coordinator.claimed == [
        "PLAN:rerank-owner",
        "PLAN:fresh-better",
        "PLAN:older-waiting",
    ]
    assert any(
        set(candidates)
        == {"PLAN:older-waiting", "PLAN:fresh-better"}
        for candidates in coordinator.arbitrated
    )
    assert coordinator.rejected == []


def test_busy_slot_invalidates_failed_first_return_when_entry_is_touched(
    tmp_path: Path,
) -> None:
    class CompetingCoordinator(_TwoPlanCoordinator):
        def push_bar(self, bar: Bar) -> list[TradePlan]:
            symbols = self.pending.setdefault(bar.close_time_ns, set())
            symbols.add(bar.symbol)
            if self.emitted or symbols != set(SYMBOLS):
                return []
            self.emitted = True
            return [
                _plan("BTCUSDT", "touch-owner", bar.close_time_ns),
                _plan(
                    "SOLUSDT",
                    "touch-invalidated",
                    bar.close_time_ns,
                    entry=20.0,
                    entry_event="FAILED_AUCTION_FUTURE_FIRST_RETURN",
                ),
            ]

    bars: list[Bar] = []
    for minute in range(4):
        for symbol in SYMBOLS:
            if symbol == "SOLUSDT":
                values = (
                    (20.2, 20.3, 20.1, 20.2)
                    if minute == 0
                    else (20.1, 20.2, 19.9, 20.0)
                )
            elif symbol == "BTCUSDT" and minute >= 1:
                values = (100.5, 101.5, 99.5, 100.5)
            else:
                base = BASE[symbol]
                values = (base * 1.01, base * 1.015, base * 1.005, base * 1.01)
            bars.append(
                _bar(
                    symbol,
                    minute,
                    open_=values[0],
                    high=values[1],
                    low=values[2],
                    close=values[3],
                ),
            )

    coordinator = CompetingCoordinator()
    state_path = tmp_path / "busy-failed-touched.sqlite3"
    result = run_native_backtest(
        bars,
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    assert result.parent_orders_submitted == 1
    assert coordinator.rejected == [
        ("PLAN:touch-invalidated", "FIRST_RETURN_PASSED_WHILE_GLOBAL_SLOT_BUSY"),
    ]
    events = _runtime_events(state_path)
    assert any(
        kind == "WAITING_GLOBAL_SLOT_INVALIDATED"
        and payload["plan_id"] == "PLAN:touch-invalidated"
        and payload["reason"] == "FIRST_RETURN_PASSED_WHILE_GLOBAL_SLOT_BUSY"
        for kind, payload in events
    )
    assert not any(
        kind == "PARENT_ORDER_SUBMITTED"
        and payload["plan"]["plan_id"] == "PLAN:touch-invalidated"
        for kind, payload in events
    )


def test_acceptance_first_response_is_bounded_ioc_not_later_return(tmp_path: Path) -> None:
    def accepted_plan(decision_ns: int) -> TradePlan:
        return TradePlan(
            episode_id="EP:accepted-now",
            plan_id="PLAN:accepted-now",
            symbol="SOLUSDT",
            family="ACCEPTED_AUCTION_CONTINUATION",
            side="LONG",
            decision_time_ns=decision_ns,
            entry=20.2,
            stop=19.8,
            target=20.8,
            expires_time_ns=10 * MINUTE,
            source_boundary_id="SRC:accepted-now",
            destination_boundary_id="DST:accepted-now",
            entry_zone=EntryZone(
                kind="SOURCE_BOUNDARY_RETEST",
                lower=20.1,
                upper=20.3,
                observed_time_ns=decision_ns,
                source_bar_open_time_ns=0,
            ),
            evidence={"entry_event": "ACCEPTANCE_FIRST_RESPONSE_CLOSE"},
        )

    coordinator = _SinglePlanCoordinator(accepted_plan)
    bars: list[Bar] = []
    for minute in range(4):
        for symbol in SYMBOLS:
            base = BASE[symbol]
            if symbol == "SOLUSDT":
                # After the response close, price never returns to 20.2.  The
                # bounded IOC may cross only as far as the native 1R boundary;
                # it can never become a resting second-return order.
                close = 20.29 if minute == 0 else 20.4
                high = 20.9 if minute == 2 else close + 0.1
                bars.append(
                    _bar(
                        symbol,
                        minute,
                        open_=close,
                        high=high,
                        low=close - 0.1,
                        close=close,
                    ),
                )
            else:
                close = base * 1.01
                bars.append(
                    _bar(
                        symbol,
                        minute,
                        open_=close,
                        high=close * 1.001,
                        low=close * 0.999,
                        close=close,
                    ),
                )

    state_path = tmp_path / "accepted-response-ioc.sqlite3"
    result = run_native_backtest(
        bars,
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    assert result.parent_orders_submitted == 1
    assert coordinator.claimed == ["PLAN:accepted-now"]
    parent = result.fills.iloc[0]
    assert str(parent["type"]) == "LIMIT"
    assert str(parent["time_in_force"]) == "IOC"
    assert float(parent["avg_px"]) >= 20.29
    assert float(parent["avg_px"]) <= 20.3
    assert str(parent["instrument_id"]) == "SOLUSDT-PERP.BINANCE"
    with sqlite3.connect(state_path) as connection:
        event_rows = [
            (event_type, json.loads(payload))
            for event_type, payload in connection.execute(
                "SELECT event_type, payload_json FROM runtime_events ORDER BY sequence",
            )
        ]
    event_types = [event_type for event_type, _ in event_rows]
    assert "PARENT_ORDER_SUBMITTED" in event_types
    assert "PARENT_LIMIT_SUBMITTED" not in event_types
    parent_event = next(payload for event_type, payload in event_rows if event_type == "PARENT_ORDER_SUBMITTED")
    sizing = parent_event["sizing"]
    assert abs(float(sizing["planned_structural_risk_fraction"]) - 0.03) < 0.0005
    assert float(sizing["planned_entry_price"]) == 20.2
    assert float(sizing["execution_limit_price"]) == 20.3
    assert float(sizing["quantity"]) == 7500.0
    assert float(sizing["planned_structural_stop_loss"]) == 3000.0
    assert float(sizing["estimated_all_in_stop_loss"]) > 3000.0
    assert float(parent_event["sizing"]["native_gross_rr"]) >= 1.0
    ledger, diagnostics = build_closed_trade_ledger(
        result.positions,
        result.fills,
        state_path=state_path,
    )
    assert diagnostics["exact_plan_joins"] == 1
    assert ledger[0]["plan_id"] == "PLAN:accepted-now"
    assert ledger[0]["exit_reason"] == "TARGET"


def test_acceptance_ioc_gap_beyond_one_r_bound_never_fills_or_claims(tmp_path: Path) -> None:
    def accepted_plan(decision_ns: int) -> TradePlan:
        return TradePlan(
            episode_id="EP:accepted-gap",
            plan_id="PLAN:accepted-gap",
            symbol="SOLUSDT",
            family="ACCEPTED_AUCTION_CONTINUATION",
            side="LONG",
            decision_time_ns=decision_ns,
            entry=20.2,
            stop=19.8,
            target=20.8,
            expires_time_ns=10 * MINUTE,
            source_boundary_id="SRC:accepted-gap",
            destination_boundary_id="DST:accepted-gap",
            entry_zone=EntryZone(
                kind="SOURCE_BOUNDARY_RETEST",
                lower=20.1,
                upper=20.3,
                observed_time_ns=decision_ns,
                source_bar_open_time_ns=0,
            ),
            evidence={"entry_event": "ACCEPTANCE_FIRST_RESPONSE_CLOSE"},
        )

    coordinator = _SinglePlanCoordinator(accepted_plan)
    bars: list[Bar] = []
    for minute in range(4):
        for symbol in SYMBOLS:
            if symbol == "SOLUSDT":
                close = 20.5
                bars.append(
                    _bar(symbol, minute, open_=close, high=close + 0.1, low=close - 0.1, close=close),
                )
            else:
                base = BASE[symbol]
                bars.append(_bar(symbol, minute, open_=base, high=base * 1.001, low=base * 0.999, close=base))

    result = run_native_backtest(
        bars,
        state_path=tmp_path / "accepted-gap.sqlite3",
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    assert result.parent_orders_submitted == 1
    assert result.fills.empty
    assert coordinator.claimed == []
    assert coordinator.rejected == [("PLAN:accepted-gap", "IMMEDIATE_RESPONSE_NOT_FILLED")]


def test_native_rounding_below_one_r_terminally_rejects_plan(tmp_path: Path) -> None:
    def rounding_plan(decision_ns: int) -> TradePlan:
        return TradePlan(
            episode_id="EP:rounding",
            plan_id="PLAN:rounding",
            symbol="BTCUSDT",
            family="FAILED_AUCTION_REVERSAL",
            side="LONG",
            decision_time_ns=decision_ns,
            entry=100.0,
            stop=98.06,
            target=101.94,
            expires_time_ns=10 * MINUTE,
            source_boundary_id="SRC:rounding",
            destination_boundary_id="DST:rounding",
            entry_zone=EntryZone(
                kind="SOURCE_BOUNDARY_RETEST",
                lower=99.5,
                upper=100.5,
                observed_time_ns=decision_ns,
                source_bar_open_time_ns=0,
            ),
            evidence={"entry_event": "FAILED_AUCTION_FUTURE_FIRST_RETURN"},
        )

    coordinator = _SinglePlanCoordinator(rounding_plan)
    result = run_native_backtest(
        _bars(),
        state_path=tmp_path / "native-rounding.sqlite3",
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    assert result.parent_orders_submitted == 0
    assert coordinator.rejected == [("PLAN:rounding", "NATIVE_GROSS_RR_BELOW_ONE")]


def test_terminal_rejection_submits_next_winner_in_same_decision_cycle(tmp_path: Path) -> None:
    def invalid_first(decision_ns: int) -> TradePlan:
        return TradePlan(
            episode_id="EP:first-invalid",
            plan_id="PLAN:first-invalid",
            symbol="BTCUSDT",
            family="FAILED_AUCTION_REVERSAL",
            side="LONG",
            decision_time_ns=decision_ns,
            entry=100.0,
            stop=98.06,
            target=101.94,
            expires_time_ns=10 * MINUTE,
            source_boundary_id="SRC:first-invalid",
            destination_boundary_id="DST:first-invalid",
            entry_zone=EntryZone("SOURCE_BOUNDARY_RETEST", 99.5, 100.5, decision_ns, 0),
            evidence={"entry_event": "FAILED_AUCTION_FUTURE_FIRST_RETURN"},
        )

    def accepted_second(decision_ns: int) -> TradePlan:
        return TradePlan(
            episode_id="EP:second-now",
            plan_id="PLAN:second-now",
            symbol="SOLUSDT",
            family="ACCEPTED_AUCTION_CONTINUATION",
            side="LONG",
            decision_time_ns=decision_ns,
            entry=20.2,
            stop=19.8,
            target=20.8,
            expires_time_ns=10 * MINUTE,
            source_boundary_id="SRC:second-now",
            destination_boundary_id="DST:second-now",
            entry_zone=EntryZone("SOURCE_BOUNDARY_RETEST", 20.1, 20.3, decision_ns, 0),
            evidence={"entry_event": "ACCEPTANCE_FIRST_RESPONSE_CLOSE"},
        )

    class CascadeCoordinator(_SinglePlanCoordinator):
        def __init__(self) -> None:
            super().__init__(invalid_first)
            self.second: TradePlan | None = None

        def reject_proposal(self, plan: TradePlan, reason: str) -> list[TradePlan]:
            self.rejected.append((plan.plan_id, reason))
            if plan.plan_id == "PLAN:first-invalid":
                self.second = accepted_second(plan.decision_time_ns)
                return [self.second]
            return []

    coordinator = CascadeCoordinator()
    state_path = tmp_path / "same-cycle-cascade.sqlite3"
    result = run_native_backtest(
        _bars(),
        state_path=state_path,
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    assert coordinator.rejected == [("PLAN:first-invalid", "NATIVE_GROSS_RR_BELOW_ONE")]
    assert coordinator.claimed == ["PLAN:second-now"]
    assert result.parent_orders_submitted == 1
    assert str(result.fills.iloc[0]["instrument_id"]) == "SOLUSDT-PERP.BINANCE"
    with sqlite3.connect(state_path) as connection:
        submitted_time, payload_json = connection.execute(
            "SELECT time_ns, payload_json FROM runtime_events "
            "WHERE event_type = 'PARENT_ORDER_SUBMITTED'",
        ).fetchone()
    assert submitted_time == coordinator.second.decision_time_ns
    assert json.loads(payload_json)["plan"]["plan_id"] == "PLAN:second-now"


def test_later_ambiguous_short_bar_cancels_target_before_native_stop(
    tmp_path: Path,
) -> None:
    def short_plan(decision_ns: int) -> TradePlan:
        return TradePlan(
            episode_id="EP:short-both",
            plan_id="PLAN:short-both",
            symbol="SOLUSDT",
            family="FAILED_AUCTION_REVERSAL",
            side="SHORT",
            decision_time_ns=decision_ns,
            entry=20.0,
            stop=20.4,
            target=19.4,
            expires_time_ns=10 * MINUTE,
            source_boundary_id="SRC:short-both",
            destination_boundary_id="DST:short-both",
            entry_zone=EntryZone(
                kind="SOURCE_BOUNDARY_RETEST",
                lower=19.9,
                upper=20.1,
                observed_time_ns=decision_ns,
                source_bar_open_time_ns=0,
            ),
            evidence={"entry_event": "FAILED_AUCTION_FUTURE_FIRST_RETURN"},
        )

    coordinator = _SinglePlanCoordinator(short_plan)
    sol = (
        (19.8, 19.9, 19.7, 19.8),
        (19.9, 20.1, 19.8, 20.0),
        (20.0, 20.1, 19.9, 20.0),
        # Both 20.4 stop and 19.4 target are touched.  Nautilus' adaptive
        # heuristic used to process the low first and incorrectly credit TP.
        (20.0, 20.8, 19.2, 20.0),
        (20.0, 20.1, 19.9, 20.0),
    )
    bars: list[Bar] = []
    for minute, (open_, high, low, close) in enumerate(sol):
        for symbol in SYMBOLS:
            if symbol == "SOLUSDT":
                bars.append(
                    _bar(symbol, minute, open_=open_, high=high, low=low, close=close),
                )
            else:
                base = BASE[symbol]
                close_peer = base * 1.01
                bars.append(
                    _bar(
                        symbol,
                        minute,
                        open_=close_peer,
                        high=close_peer * 1.001,
                        low=close_peer * 0.999,
                        close=close_peer,
                    ),
                )

    result = run_native_backtest(
        bars,
        state_path=tmp_path / "short-adverse-first.sqlite3",
        configure_strategy=lambda strategy: setattr(strategy, "coordinator", coordinator),
    )

    exits = result.fills.iloc[1:]
    assert set(exits["type"].astype(str)) == {"STOP_MARKET"}
    assert result.adverse_first_target_cancels >= 1
    assert result.final_balance < 100_000


def _configure_injected_protection_rejection(strategy, coordinator) -> None:
    """Inject one exchange-style reject while retaining native account state."""

    strategy.coordinator = coordinator
    original = strategy._submit_protection
    fired = False

    def submit_then_reject(quantity, *, fill_time_ns: int) -> None:
        nonlocal fired
        original(quantity, fill_time_ns=fill_time_ns)
        if fired:
            return
        stop_id = next(
            (key for key, role in strategy.order_roles.items() if role == "STOP"),
            None,
        )
        if stop_id is None:
            return
        fired = True
        plan = strategy.active_plan
        assert plan is not None
        strategy.on_order_rejected(
            SimpleNamespace(
                client_order_id=ClientOrderId(stop_id),
                instrument_id=strategy.instrument_ids[plan.symbol],
                reason="INJECTED_PROTECTION_REJECTION",
                ts_event=fill_time_ns,
            ),
        )

    strategy._submit_protection = submit_then_reject


def test_true_protection_failure_emergency_flattens_raced_parent_chunks(tmp_path: Path) -> None:
    state_path = tmp_path / "protection-reject.sqlite3"
    coordinator = _TwoPlanCoordinator()
    result = run_native_backtest(
        _bars(),
        state_path=state_path,
        configure_strategy=lambda strategy: _configure_injected_protection_rejection(
            strategy,
            coordinator,
        ),
    )

    with sqlite3.connect(state_path) as connection:
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM runtime_events ORDER BY sequence",
            )
        ]
    assert "EMERGENCY_FLATTEN_SUBMITTED" in event_types
    assert "EXECUTION_HALT" in event_types
    # The parent cancel can race another fill after protection failed.  That
    # exact chunk receives its own reduce-only emergency exit.
    assert "EMERGENCY_RACE_FILL_EXIT_SUBMITTED" in event_types
    assert set(result.positions["side"].astype(str)) == {"FLAT"}
    assert result.max_active_instruments == 1


def test_late_protective_rejection_when_native_position_is_flat_does_not_halt(tmp_path: Path) -> None:
    state_path = tmp_path / "late-flat-reject.sqlite3"
    coordinator = _SolPartialCoordinator()

    def configure(strategy) -> None:
        strategy.coordinator = coordinator
        original_protection = strategy._submit_protection
        original_closed = strategy.on_position_closed
        captured: list[str] = []
        injected = False

        def capture(quantity, *, fill_time_ns: int) -> None:
            original_protection(quantity, fill_time_ns=fill_time_ns)
            captured.extend(
                key for key, role in strategy.order_roles.items()
                if role == "STOP" and key not in captured
            )

        def late_reject(event) -> None:
            nonlocal injected
            if not injected and captured:
                injected = True
                order_id = captured[0]
                strategy.active_order_ids.add(order_id)
                strategy.order_roles[order_id] = "STOP"
                strategy.on_order_rejected(
                    SimpleNamespace(
                        client_order_id=ClientOrderId(order_id),
                        instrument_id=event.instrument_id,
                        reason="LATE_REJECT_AFTER_FLAT",
                        ts_event=int(event.ts_event),
                    ),
                )
            original_closed(event)

        strategy._submit_protection = capture
        strategy.on_position_closed = late_reject

    result = run_native_backtest(
        _partial_fill_bars(),
        state_path=state_path,
        configure_strategy=configure,
    )

    with sqlite3.connect(state_path) as connection:
        event_types = [
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM runtime_events ORDER BY sequence",
            )
        ]
    assert "ORDER_ERROR_FLAT_RECOVERED" in event_types
    assert "EXECUTION_HALT" not in event_types
    assert set(result.positions["side"].astype(str)) == {"FLAT"}
