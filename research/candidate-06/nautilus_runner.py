"""NautilusTrader-only engine construction and report extraction for candidate-06."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

import pandas as pd

from logic import BarObservation
from nautilus_strategy import make_strategy_class


@dataclass(frozen=True, slots=True)
class NautilusRunResult:
    strategy: Any
    fills: pd.DataFrame
    positions: pd.DataFrame
    account: pd.DataFrame


def build_btcusdt_perpetual(effective_fee_rate: Decimal):
    """Build historical BTCUSDT perpetual metadata with explicit all-in fees."""
    from nautilus_trader.model.currencies import BTC, USDT
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Money, Price, Quantity

    return CryptoPerpetual(
        instrument_id=InstrumentId(symbol=Symbol("BTCUSDT-PERP"), venue=Venue("BINANCE")),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=1,
        price_increment=Price.from_str("0.1"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000.000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(10.00, USDT),
        max_price=Price.from_str("809484.0"),
        min_price=Price.from_str("261.1"),
        margin_init=Decimal("0.0500"),
        margin_maint=Decimal("0.0250"),
        maker_fee=effective_fee_rate,
        taker_fee=effective_fee_rate,
        ts_event=0,
        ts_init=0,
    )


def frame_to_observations(frame: pd.DataFrame) -> dict[int, BarObservation]:
    observations: dict[int, BarObservation] = {}
    for timestamp, row in frame.iterrows():
        ts_ns = int(timestamp.value)
        observations[ts_ns] = BarObservation(
            ts_ns=ts_ns,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            taker_buy_volume=float(row["taker_buy_volume"]),
            trades=int(row["trades"]),
        )
    return observations


def _detached_bar_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Create fresh writable column buffers for the Cython BarDataWrangler.

    Pandas may expose read-only views after concatenating checksum-verified daily
    archives.  Reconstructing from Python lists changes no values or timestamps;
    it only gives NautilusTrader writable, independently owned arrays.
    """
    columns = ("open", "high", "low", "close", "volume")
    return pd.DataFrame(
        {column: [float(value) for value in frame[column].tolist()] for column in columns},
        index=pd.DatetimeIndex(frame.index.tolist()),
    )


def run_nautilus_backtest(
    frame: pd.DataFrame,
    *,
    config: Mapping[str, Any],
    logic_params: Mapping[str, Any],
) -> NautilusRunResult:
    """Run one evaluation exclusively through NautilusTrader BacktestEngine."""
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Currency, Money
    from nautilus_trader.persistence.wranglers import BarDataWrangler

    effective_fee = Decimal(str(config["effective_fee_rate_per_fill"]))
    instrument = build_btcusdt_perpetual(effective_fee)
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
    bars_frame = _detached_bar_frame(frame)
    bars = BarDataWrangler(bar_type, instrument).process(bars_frame)
    observations = frame_to_observations(frame)
    if len(bars) != len(observations):
        raise RuntimeError(f"bar conversion mismatch: bars={len(bars)}, observations={len(observations)}")

    LRBStrategyConfig, LRBStrategy = make_strategy_class()
    strategy = LRBStrategy(
        LRBStrategyConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            starting_balance=Decimal(str(config["starting_balance_usdt"])),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            effective_fee_rate=effective_fee,
            max_holding_bars=int(logic_params["max_holding_bars"]),
            final_ts_ns=int(frame.index[-1].value),
            min_net_rr_after_delay=Decimal(str(logic_params["minimum_net_rr_after_entry_delay"])),
            max_entry_drift_atr=Decimal(str(logic_params["max_entry_drift_atr"])),
            one_tick_slippage_per_fill=bool(config["one_tick_slippage_per_fill"]),
        ),
        observations=observations,
        logic_params=logic_params,
    )
    engine = BacktestEngine(
        config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
    )
    venue = Venue("BINANCE")
    usdt = Currency.from_str("USDT")
    try:
        fill_model = FillModel(
            prob_fill_on_limit=float(config["prob_fill_on_limit_touch"]),
            prob_slippage=1.0 if config["one_tick_slippage_per_fill"] else 0.0,
            random_seed=int(config["fill_model_seed"]),
        )
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(float(config["starting_balance_usdt"]), usdt)],
            base_currency=usdt,
            default_leverage=Decimal(str(config["venue_default_leverage"])),
            fill_model=fill_model,
            support_contingent_orders=True,
            use_reduce_only=True,
            bar_execution=True,
            bar_adaptive_high_low_ordering=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        fills = engine.trader.generate_order_fills_report()
        positions = engine.trader.generate_positions_report()
        account = engine.trader.generate_account_report(venue)
        return NautilusRunResult(strategy=strategy, fills=fills, positions=positions, account=account)
    finally:
        engine.dispose()
