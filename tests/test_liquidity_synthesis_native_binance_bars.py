from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from nautilus_trader.adapters.binance import BinanceBar
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import Bar as NautilusBar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.objects import Price, Quantity

from smc_ict_4.episode_policy_live.domain import SYMBOLS
from smc_ict_4.episode_policy_live.live import (
    LiquidityEpisodeStrategy,
    LiquidityEpisodeStrategyConfig,
    MinuteTradeBuilder,
    policy_bar_from_native_binance_bar,
)
from smc_ict_4.episode_policy_live.nautilus_backtest import (
    external_bar_types,
    make_binance_perpetuals,
)
from smc_ict_4.episode_policy_live.storage import StateStore


MINUTE = 60_000_000_000


def _native_bar(
    symbol: str = "BTCUSDT",
    *,
    minute: int = 0,
    count: int = 731,
    close: str = "100.5",
    ts_event: int | None = None,
) -> BinanceBar:
    close_ns = (minute + 1) * MINUTE - 1_000_000 if ts_event is None else ts_event
    return BinanceBar(
        bar_type=BarType.from_str(
            f"{symbol}-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL",
        ),
        open=Price.from_str("100.0"),
        high=Price.from_str("101.0"),
        low=Price.from_str("99.0"),
        close=Price.from_str(close),
        volume=Quantity.from_str("10.0"),
        quote_volume=Decimal("1002.75"),
        count=count,
        taker_buy_base_volume=Decimal("5.25"),
        taker_buy_quote_volume=Decimal("526.25"),
        ts_event=close_ns,
        ts_init=(minute + 1) * MINUTE,
    )


def _strategy(
    state_path: Path,
    *,
    execution_mode: str = "BACKTEST",
) -> LiquidityEpisodeStrategy:
    instruments = make_binance_perpetuals()
    bar_types = external_bar_types(instruments)
    strategy = LiquidityEpisodeStrategy(
        LiquidityEpisodeStrategyConfig(
            instrument_ids=tuple(item.id for item in instruments.values()),
            bar_types=tuple(bar_types.values()),
            state_path=str(state_path),
            execution_mode=execution_mode,
        ),
    )
    # This focused ingestion test does not attach the strategy to a Trader;
    # global account-slot behavior is covered by native engine tests.
    strategy._observe_global_slot = lambda: None
    return strategy


def test_native_binance_bar_preserves_raw_kline_flow_and_underlying_trade_count() -> None:
    native = _native_bar(count=731)
    policy = policy_bar_from_native_binance_bar(native)

    assert policy.open_time_ns == 0
    assert policy.close_time_ns == MINUTE
    assert policy.quote_volume == 1002.75
    assert policy.taker_buy_quote_volume == 526.25
    assert policy.trade_count == 731

    # Two local aggTrade messages can represent hundreds of underlying trades.
    # This fixture remains test-only and demonstrates why production cannot use
    # its message count as Binance kline trade_count.
    legacy = MinuteTradeBuilder("BTCUSDT")
    legacy.push(ts_ns=1, price=100.0, quantity=1.0, buyer_aggressor=True)
    legacy.push(ts_ns=2, price=100.5, quantity=1.0, buyer_aggressor=False)
    reconstructed = legacy.push(
        ts_ns=MINUTE + 1,
        price=101.0,
        quantity=1.0,
        buyer_aggressor=True,
    )
    assert reconstructed is not None
    assert reconstructed.trade_count == 2
    assert policy.trade_count != reconstructed.trade_count


def test_pyo3_native_binance_payload_maps_to_identical_policy_bar() -> None:
    legacy = _native_bar(count=431)
    payload = BinanceBar.to_dict(legacy)
    pyo3_bar = nautilus_pyo3.binance.BinanceBar.from_dict(payload)

    expected_type = BarType.from_str(payload["bar_type"])
    assert policy_bar_from_native_binance_bar(
        pyo3_bar,
        expected_bar_type=expected_type,
    ) == policy_bar_from_native_binance_bar(legacy, expected_bar_type=expected_type)


def test_native_policy_conversion_rejects_non_completed_clock_instead_of_rounding() -> None:
    with pytest.raises(RuntimeError, match="non-canonical completed external bar clock"):
        policy_bar_from_native_binance_bar(_native_bar(ts_event=MINUTE - 5_000_000_000))


def test_production_rejects_plain_bar_instead_of_silent_flow_fallback(tmp_path: Path) -> None:
    strategy = _strategy(tmp_path / "no-production-fallback.sqlite", execution_mode="SHADOW")
    native = _native_bar()
    plain = NautilusBar(
        bar_type=native.bar_type,
        open=native.open,
        high=native.high,
        low=native.low,
        close=native.close,
        volume=native.volume,
        ts_event=native.ts_event,
        ts_init=native.ts_init,
    )
    try:
        with pytest.raises(RuntimeError, match="requires native BinanceBar"):
            strategy.on_bar(plain)
    finally:
        strategy.store.close()


def test_native_binance_bar_is_idempotent_across_duplicate_and_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "native-binancebar-restart.sqlite"
    native = _native_bar()

    first = _strategy(state_path)
    try:
        first.on_bar(native)
        first.on_bar(native)
    finally:
        first.store.close()

    restarted = _strategy(state_path)
    try:
        # Hydrate the same canonical-minute identity map used by startup
        # replay; full Trader/clock wiring is covered by restart engine tests.
        stored = restarted.store.load_bars(interval_minutes=1, symbols=SYMBOLS)
        restarted._known_policy_minutes = {
            (bar.symbol, bar.interval_minutes, bar.open_time_ns): bar
            for bar in stored
        }
        restarted.on_bar(native)
    finally:
        restarted.store.close()

    with StateStore(state_path) as store:
        bars = store.load_bars(interval_minutes=1, symbols=SYMBOLS)
        duplicates = store.load_events(event_types=("BAR_CLOCK_DUPLICATE_IGNORED",))
    assert len(bars) == 1
    assert bars[0] == policy_bar_from_native_binance_bar(native)
    assert len(duplicates) == 2


def test_strategy_config_exposes_no_production_tick_bar_switch() -> None:
    assert "build_bars_from_ticks" not in LiquidityEpisodeStrategyConfig.__struct_fields__
