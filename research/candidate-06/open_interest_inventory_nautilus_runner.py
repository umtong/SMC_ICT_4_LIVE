"""NautilusTrader-only runner for completed OI inventory regimes."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

import pandas as pd

from futures_metrics_data import LoadedFuturesMetrics
from nautilus_runner import (
    NautilusRunResult,
    build_btcusdt_perpetual,
    frame_to_nautilus_bars,
    frame_to_observations,
)
from nautilus_strategy import make_strategy_class
from open_interest_inventory_regime_engine import OpenInterestInventoryRegimeRelayEngine


def run_open_interest_inventory_nautilus_backtest(
    frame: pd.DataFrame,
    metrics_data: LoadedFuturesMetrics,
    *,
    config: Mapping[str, Any],
    logic_params: Mapping[str, Any],
) -> NautilusRunResult:
    from nautilus_trader.backtest.engine import BacktestEngine
    from nautilus_trader.backtest.models import FillModel
    from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
    from nautilus_trader.model.data import BarType
    from nautilus_trader.model.enums import AccountType, OmsType, OtoTriggerMode
    from nautilus_trader.model.identifiers import Venue
    from nautilus_trader.model.objects import Currency, Money

    effective_fee = Decimal(str(config["effective_fee_rate_per_fill"]))
    instrument = build_btcusdt_perpetual(effective_fee)
    bar_type = BarType.from_str("BTCUSDT-PERP.BINANCE-1-MINUTE-LAST-EXTERNAL")
    bars = frame_to_nautilus_bars(frame, bar_type=bar_type)
    observations = frame_to_observations(frame)

    BaseConfig, BaseStrategy = make_strategy_class()

    class OpenInterestInventoryStrategy(BaseStrategy):
        def __init__(self, strategy_config: Any) -> None:
            base_logic = dict(logic_params)
            base_logic["engine"] = "LIQUIDITY_RESPONSE_BIFURCATION"
            super().__init__(
                strategy_config,
                observations=observations,
                logic_params=base_logic,
            )
            self._logic_params = dict(logic_params)
            self._scenario_engine = OpenInterestInventoryRegimeRelayEngine(
                logic_params,
                metrics=metrics_data.observations,
            )
            self.diagnostics["futures_metrics_context"] = dict(metrics_data.quality)

    strategy = OpenInterestInventoryStrategy(
        BaseConfig(
            instrument_id=instrument.id,
            bar_type=bar_type,
            starting_balance=Decimal(str(config["starting_balance_usdt"])),
            risk_fraction=Decimal(str(config["risk_fraction"])),
            effective_fee_rate=effective_fee,
            max_holding_bars=int(logic_params["max_holding_bars"]),
            final_ts_ns=int(frame.index[-1].value),
            min_net_rr_after_delay=Decimal(
                str(logic_params["minimum_net_rr_after_entry_delay"]),
            ),
            max_entry_drift_atr=Decimal(str(logic_params["max_entry_drift_atr"])),
            one_tick_slippage_per_fill=bool(config["one_tick_slippage_per_fill"]),
        ),
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
            oto_trigger_mode=OtoTriggerMode.PARTIAL,
            use_reduce_only=True,
            bar_execution=True,
            bar_adaptive_high_low_ordering=True,
        )
        engine.add_instrument(instrument)
        engine.add_data(bars)
        engine.add_strategy(strategy)
        engine.run()
        return NautilusRunResult(
            strategy=strategy,
            fills=engine.trader.generate_order_fills_report(),
            positions=engine.trader.generate_positions_report(),
            account=engine.trader.generate_account_report(venue),
        )
    finally:
        engine.dispose()
