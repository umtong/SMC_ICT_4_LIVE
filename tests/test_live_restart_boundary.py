"""Executable restart boundaries for process-local and exchange-backed modes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from smc_ict_4.episode_policy_live.live import (
    LiquidityEpisodeStrategy,
    LiquidityEpisodeStrategyConfig,
    MinuteTradeBuilder,
    bootstrap_store,
    fetch_recent_binance_bars,
    native_restart_block_reason,
    native_restart_capabilities,
    run_node,
    run_node_blocking,
)
from smc_ict_4.episode_policy_live.domain import Bar, SYMBOLS
from smc_ict_4.episode_policy_live.nautilus_backtest import (
    build_native_backtest,
    external_bar_types,
    make_binance_perpetuals,
)
from smc_ict_4.episode_policy_live.storage import StateStore


MINUTE = 60_000_000_000


def _clock_bar(
    symbol: str,
    *,
    close_time_ns: int,
    open_time_ns: int = 0,
    close: float = 100.5,
) -> Bar:
    return Bar(
        symbol=symbol,
        interval_minutes=1,
        open_time_ns=open_time_ns,
        close_time_ns=close_time_ns,
        open=100.0,
        high=max(101.0, close),
        low=min(99.0, close),
        close=close,
        volume=10.0,
        quote_volume=1_000.0,
        taker_buy_quote_volume=550.0,
        trade_count=10,
    )


class _Response:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_rest_and_tick_bars_share_the_exact_exclusive_right_edge(monkeypatch) -> None:
    import smc_ict_4.episode_policy_live.live as live_module

    delegated = _clock_bar("BTCUSDT", close_time_ns=MINUTE)
    calls: list[tuple[str, int, int]] = []
    monkeypatch.setattr(
        live_module,
        "_fetch_recent_binance_bars",
        lambda symbol, *, limit, clock_ns: (
            calls.append((symbol, limit, clock_ns())) or [delegated]
        ),
    )
    rest = fetch_recent_binance_bars("BTCUSDT", limit=1, clock_ns=lambda: 123)[0]

    builder = MinuteTradeBuilder("BTCUSDT")
    assert builder.push(ts_ns=1, price=100.0, quantity=1.0, buyer_aggressor=True) is None
    tick = builder.push(
        ts_ns=MINUTE + 1,
        price=101.0,
        quantity=1.0,
        buyer_aggressor=False,
    )
    assert tick is not None
    assert (rest.open_time_ns, rest.close_time_ns) == (0, MINUTE)
    assert (tick.open_time_ns, tick.close_time_ns) == (0, MINUTE)
    assert calls == [("BTCUSDT", 1, 123)]


def test_bootstrap_does_not_store_canonical_alias_beside_legacy_minute(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import smc_ict_4.episode_policy_live.live as live_module

    state_path = tmp_path / "legacy-clock.sqlite"
    legacy: dict[str, Bar] = {}
    with StateStore(state_path) as store:
        for index, symbol in enumerate(SYMBOLS):
            raw_close = MINUTE - (1_000_000 if index % 2 == 0 else 1)
            legacy[symbol] = _clock_bar(symbol, close_time_ns=raw_close)
            assert store.append_bar(legacy[symbol])

        canonical = {
            symbol: _clock_bar(symbol, close_time_ns=MINUTE)
            for symbol in SYMBOLS
        }
        monkeypatch.setattr(
            live_module,
            "fetch_recent_binance_bars",
            lambda symbol, **_kwargs: [canonical[symbol]],
        )
        counts = bootstrap_store(store, limit=1)
        row_count = store.connection.execute("SELECT COUNT(*) AS count FROM bars").fetchone()
        clock_event = store.connection.execute(
            "SELECT payload_json FROM runtime_events "
            "WHERE event_type='BAR_CLOCK_COMPATIBILITY_NORMALIZED' "
            "ORDER BY sequence DESC LIMIT 1",
        ).fetchone()

    assert counts == {symbol: 0 for symbol in SYMBOLS}
    assert row_count["count"] == 4
    assert json.loads(clock_event["payload_json"])["legacy_clock_rows_normalized"] == 4


def test_bootstrap_fetches_all_four_complete_windows_before_any_insert(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import smc_ict_4.episode_policy_live.live as live_module

    state_path = tmp_path / "all-or-no-startup.sqlite"

    def fetch(symbol: str, **_kwargs) -> list[Bar]:
        if symbol == "XRPUSDT":
            raise RuntimeError("injected fourth-symbol failure")
        return [_clock_bar(symbol, close_time_ns=MINUTE)]

    monkeypatch.setattr(live_module, "fetch_recent_binance_bars", fetch)
    with StateStore(state_path) as store:
        with pytest.raises(RuntimeError, match="fourth-symbol failure"):
            bootstrap_store(store, limit=1)
        assert store.counts()["bars"] == 0


def test_bootstrap_freezes_one_clock_and_one_window_across_sequential_fetches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import smc_ict_4.episode_policy_live.live as live_module

    clock_samples = iter(
        [
            100 * MINUTE + MINUTE - 1,
            101 * MINUTE + 1,
            102 * MINUTE + 1,
            103 * MINUTE + 1,
            104 * MINUTE + 1,
        ],
    )
    monkeypatch.setattr(live_module.time, "time_ns", lambda: next(clock_samples))
    observed_clocks: list[int] = []

    def fetch(symbol: str, *, limit: int, clock_ns) -> list[Bar]:
        frozen = clock_ns()
        observed_clocks.append(frozen)
        newest_slot = (frozen - 1) // MINUTE - 1
        first_slot = newest_slot - limit + 1
        return [
            _clock_bar(
                symbol,
                open_time_ns=slot * MINUTE,
                close_time_ns=(slot + 1) * MINUTE,
            )
            for slot in range(first_slot, newest_slot + 1)
        ]

    monkeypatch.setattr(live_module, "fetch_recent_binance_bars", fetch)
    with StateStore(tmp_path / "frozen-bootstrap-window.sqlite") as store:
        counts = bootstrap_store(store, limit=3)
        stored = store.load_bars(interval_minutes=1, symbols=SYMBOLS)

    assert observed_clocks == [100 * MINUTE + MINUTE - 1] * len(SYMBOLS)
    assert counts == {symbol: 3 for symbol in SYMBOLS}
    opens_by_symbol = {
        symbol: [bar.open_time_ns for bar in stored if bar.symbol == symbol]
        for symbol in SYMBOLS
    }
    assert len({tuple(opens) for opens in opens_by_symbol.values()}) == 1
    assert all(opens[0] == 97 * MINUTE and opens[-1] == 99 * MINUTE for opens in opens_by_symbol.values())


def test_prepare_passes_one_explicit_lookback_to_bootstrap_and_strategy_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import smc_ict_4.episode_policy_live.live as live_module

    calls: dict[str, object] = {}

    class Collector:
        def poll_all(self) -> tuple[object, ...]:
            return ()

    strategy = SimpleNamespace(live_inventory_collector=None)
    node = SimpleNamespace(_episode_policy_strategy=strategy)

    monkeypatch.setattr(live_module, "LiveInventoryCollector", Collector)
    monkeypatch.setattr(
        live_module,
        "bootstrap_store",
        lambda _store, *, limit: calls.setdefault("bootstrap", limit),
    )

    def build_node(**kwargs):
        calls["build"] = kwargs
        return node

    monkeypatch.setattr(live_module, "build_node", build_node)
    monkeypatch.setattr(
        live_module,
        "apply_live_inventory_results",
        lambda _strategy, results: calls.setdefault("inventory", results),
    )

    prepared = live_module._prepare_runtime_node(
        execution_mode="SHADOW",
        state_path=tmp_path / "prepare-lookback.sqlite",
        bootstrap=True,
        bootstrap_lookback_minutes=37,
    )
    assert prepared is node
    assert calls["bootstrap"] == 37
    assert calls["build"]["bootstrap_lookback_minutes"] == 37
    assert strategy.live_inventory_collector is not None


def test_legacy_four_symbol_rows_replay_on_one_canonical_clock(tmp_path: Path) -> None:
    state_path = tmp_path / "legacy-sync.sqlite"
    with StateStore(state_path) as store:
        for index, symbol in enumerate(SYMBOLS):
            raw_close = MINUTE - (1_000_000 if index % 2 == 0 else 1)
            assert store.append_bar(_clock_bar(symbol, close_time_ns=raw_close))

    class RecordingCoordinator:
        def __init__(self) -> None:
            self.bars: list[Bar] = []

        def push_bar(self, bar: Bar) -> list[object]:
            self.bars.append(bar)
            return []

        def export_state(self) -> dict[str, object]:
            return {"version": 1}

    recorder = RecordingCoordinator()
    live_group = [
        _clock_bar(symbol, open_time_ns=MINUTE, close_time_ns=2 * MINUTE)
        for symbol in SYMBOLS
    ]
    session = build_native_backtest(live_group, state_path=state_path)
    session.strategy.coordinator = recorder
    try:
        session.run()
        replayed = [bar for bar in recorder.bars if bar.close_time_ns == MINUTE]
        live = [bar for bar in recorder.bars if bar.close_time_ns == 2 * MINUTE]
        assert {bar.symbol for bar in replayed} == set(SYMBOLS)
        assert {bar.close_time_ns for bar in replayed} == {MINUTE}
        assert {bar.symbol for bar in live} == set(SYMBOLS)
        assert {bar.close_time_ns for bar in live} == {2 * MINUTE}
    finally:
        session.dispose()


def test_partial_crash_minute_is_seeded_then_completed_exactly_once(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "partial-minute.sqlite"
    with StateStore(state_path) as store:
        for symbol in ("BTCUSDT", "ETHUSDT"):
            assert store.append_bar(_clock_bar(symbol, close_time_ns=MINUTE))

    reconnect_group = [
        _clock_bar(symbol, close_time_ns=MINUTE)
        for symbol in SYMBOLS
    ]
    session = build_native_backtest(reconnect_group, state_path=state_path)
    try:
        session.run()
        policies = session.strategy.coordinator.policies
        assert {
            symbol: len(policies[symbol].market.one_minute)
            for symbol in SYMBOLS
        } == {symbol: 1 for symbol in SYMBOLS}
        assert session.strategy.coordinator._pending_by_close == {}
    finally:
        session.dispose()

    with StateStore(state_path) as store:
        assert store.counts()["bars"] == 4
        replay = store.connection.execute(
            "SELECT payload_json FROM runtime_events "
            "WHERE event_type='STATE_REPLAYED' ORDER BY sequence DESC LIMIT 1",
        ).fetchone()
    payload = json.loads(replay["payload_json"])
    assert payload["complete_four_symbol_minutes"] == 0
    assert payload["pending_partial_minute_members"] == 2


def test_partial_crash_member_mutation_still_fails_closed(tmp_path: Path) -> None:
    state_path = tmp_path / "partial-minute-mutation.sqlite"
    with StateStore(state_path) as store:
        assert store.append_bar(_clock_bar("BTCUSDT", close_time_ns=MINUTE))

    reconnect_group = [
        _clock_bar(
            symbol,
            close_time_ns=MINUTE,
            close=100.75 if symbol == "BTCUSDT" else 100.5,
        )
        for symbol in SYMBOLS
    ]
    session = build_native_backtest(reconnect_group, state_path=state_path)
    try:
        with pytest.raises(RuntimeError, match="live market data mutation"):
            session.run()
    finally:
        session.dispose()


def test_replay_fails_closed_on_conflicting_clock_aliases(tmp_path: Path) -> None:
    state_path = tmp_path / "legacy-conflict.sqlite"
    with StateStore(state_path) as store:
        assert store.append_bar(_clock_bar("BTCUSDT", close_time_ns=MINUTE - 1_000_000))
        # SQLite's historical close-time PK can contain this second alias;
        # replay must never treat it as another causal observation.
        assert store.append_bar(
            _clock_bar("BTCUSDT", close_time_ns=MINUTE, close=100.75),
        )

    live_group = [
        _clock_bar(symbol, open_time_ns=MINUTE, close_time_ns=2 * MINUTE)
        for symbol in SYMBOLS
    ]
    session = build_native_backtest(live_group, state_path=state_path)
    try:
        with pytest.raises(RuntimeError, match="conflicting stored bars share one canonical minute"):
            session.run()
    finally:
        session.dispose()


def test_pinned_native_runtime_reports_honest_restart_capabilities() -> None:
    report = native_restart_capabilities()
    assert report["cache_database_types"] == ["redis"]
    assert report["external_cache_configured"] is False
    assert report["sqlite_restores_native_account"] is False
    assert report["shadow_sandbox_restart"] == "FAIL_CLOSED_AFTER_NATIVE_ORDER_OR_FILL"
    assert report["testnet_restart"] == (
        "EXPERIMENTAL_PARTIAL_EXCHANGE_REPORT_RECONCILIATION"
    )


def test_process_local_sandbox_state_blocks_but_testnet_routes_to_exchange_reconciliation() -> None:
    runtime = {
        "mode": "SHADOW",
        "active_plan": None,
        "active_order_ids": [],
        "sandbox_native_account_mutated": True,
    }
    assert native_restart_block_reason("SHADOW", runtime) == (
        "PROCESS_LOCAL_SANDBOX_ACCOUNT_HISTORY_NOT_RESTORABLE"
    )
    assert native_restart_block_reason("TESTNET", {**runtime, "mode": "TESTNET"}) is None
    assert native_restart_block_reason("SANDBOX", runtime) == "EXECUTION_MODE_CHANGED:SHADOW->SANDBOX"


def test_strategy_reads_sqlite_intent_as_fail_closed_boundary_not_native_restore(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "shadow-restart.sqlite"
    with StateStore(state_path) as store:
        store.save_snapshot(
            "strategy_runtime",
            time_ns=1,
            payload={
                "mode": "SHADOW",
                "active_plan": None,
                "active_order_ids": [],
                "sandbox_native_account_mutated": True,
            },
        )
    instruments = make_binance_perpetuals()
    bar_types = external_bar_types(instruments)
    strategy = LiquidityEpisodeStrategy(
        LiquidityEpisodeStrategyConfig(
            instrument_ids=tuple(item.id for item in instruments.values()),
            bar_types=tuple(bar_types.values()),
            state_path=str(state_path),
            execution_mode="SHADOW",
        ),
    )
    try:
        assert strategy._restart_block_reason == (
            "PROCESS_LOCAL_SANDBOX_ACCOUNT_HISTORY_NOT_RESTORABLE"
        )
        assert strategy.active_plan is None
    finally:
        strategy.store.close()


def test_flat_never_executed_sandbox_snapshot_can_restart() -> None:
    runtime = {
        "mode": "SANDBOX",
        "active_plan": None,
        "active_order_ids": [],
        "sandbox_native_account_mutated": False,
        "emergency_flatten_pending": False,
    }
    assert native_restart_block_reason("SANDBOX", runtime) is None


def test_run_node_refuses_process_local_account_restart_before_network_bootstrap(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "blocked-shadow.sqlite"
    with StateStore(state_path) as store:
        store.save_snapshot(
            "strategy_runtime",
            time_ns=1,
            payload={
                "mode": "SHADOW",
                "active_plan": None,
                "active_order_ids": [],
                "sandbox_native_account_mutated": True,
            },
        )
    with pytest.raises(RuntimeError, match="cannot restore the process-local"):
        asyncio.run(
            run_node(
                execution_mode="SHADOW",
                state_path=state_path,
                duration_seconds=1,
                bootstrap=True,
            ),
        )
    with StateStore(state_path) as store:
        latest = store.connection.execute(
            "SELECT event_type FROM runtime_events ORDER BY sequence DESC LIMIT 1",
        ).fetchone()
    assert latest["event_type"] == "NATIVE_RESTART_PREFLIGHT_BLOCKED"


def test_blocking_lifecycle_disposes_only_after_native_loop_stops(monkeypatch) -> None:
    import smc_ict_4.episode_policy_live.live as live_module

    class FakeKernel:
        def __init__(self) -> None:
            self.loop = asyncio.new_event_loop()

    class FakeNode:
        def __init__(self) -> None:
            self.kernel = FakeKernel()
            self.stopped = asyncio.Event()
            self.dispose_saw_running_loop: bool | None = None

        async def run_async(self) -> None:
            await self.stopped.wait()

        async def stop_async(self) -> None:
            self.stopped.set()

        def dispose(self) -> None:
            self.dispose_saw_running_loop = self.kernel.loop.is_running()
            self.kernel.loop.close()

    node = FakeNode()
    monkeypatch.setattr(live_module, "_prepare_runtime_node", lambda **_: node)

    run_node_blocking(
        execution_mode="SHADOW",
        state_path=Path("unused.sqlite"),
        duration_seconds=0,
        bootstrap=False,
    )

    assert node.dispose_saw_running_loop is False
    assert node.kernel.loop.is_closed()


def test_bounded_runtime_starts_only_after_node_is_running() -> None:
    import smc_ict_4.episode_policy_live.live as live_module

    class DelayedNode:
        def __init__(self) -> None:
            self.running = False
            self.stopped = asyncio.Event()

        def is_running(self) -> bool:
            return self.running

        async def run_async(self) -> None:
            await asyncio.sleep(0.05)
            self.running = True
            await self.stopped.wait()

        async def stop_async(self) -> None:
            self.stopped.set()

    async def exercise() -> float:
        loop = asyncio.get_running_loop()
        started = loop.time()
        await live_module._run_prepared_node(DelayedNode(), duration_seconds=0.05)
        return loop.time() - started

    assert asyncio.run(exercise()) >= 0.09
