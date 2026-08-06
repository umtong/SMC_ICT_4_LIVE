#!/usr/bin/env python3
"""Non-performance smoke for the authoritative TradeTick order path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from aggtrade_data import AggTrade
from core import Side
from impact_regime_probe import ScenarioPlan
from nautilus_plan_backtest import NautilusExecutionConfig
from nautilus_tick_plan_backtest import run_nautilus_tick_plan_backtest


def main() -> int:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=8)
    start_ns = int(start.timestamp() * 1_000_000_000)
    prices = (100.0, 100.0, 100.1, 100.2, 102.1, 102.0, 101.9, 101.8, 101.7, 101.6, 101.5)
    trades = [
        AggTrade(
            agg_trade_id=index + 1,
            price=price,
            quantity=1.0,
            first_trade_id=index + 1,
            last_trade_id=index + 1,
            ts_event_ns=start_ns + index * 1_000_000_000,
            is_buyer_maker=False,
        )
        for index, price in enumerate(prices)
    ]
    plan = ScenarioPlan(
        scenario_id="tick-smoke-long",
        response="CONTINUATION",
        side=Side.LONG,
        signal_bar_index=0,
        signal_time_ns=start_ns + 1_000_000_000,
        stop_price=99.0,
        target_price=102.0,
        confirmation_hold_price=99.9,
        structure_high=102.0,
        structure_low=99.0,
        structure_midpoint=100.5,
        pulse_high=100.1,
        pulse_low=99.9,
        pulse_flow_score=1.0,
        pulse_move_atr=1.0,
        pulse_path_efficiency=1.0,
        pulse_close_location=1.0,
        reason_code="TRADETICK_ADAPTER_SMOKE",
    )
    execution = NautilusExecutionConfig(
        starting_nav=100_000.0,
        risk_fraction=0.00001,
        all_in_cost_bps_per_side=7.0,
        minimum_net_reward_risk=1.35,
        venue_max_leverage=125.0,
        minimum_price_risk_fraction=0.65,
        price_precision=1,
        quantity_precision=3,
        price_increment=0.1,
        quantity_increment=0.001,
    )
    with tempfile.TemporaryDirectory(prefix="candidate01-tick-smoke-") as directory:
        evidence = run_nautilus_tick_plan_backtest(
            label="tick-smoke",
            trades=trades,
            plans=[plan],
            evaluation_start=start,
            evaluation_end=end,
            execution=execution,
            maximum_hold_ns=5_000_000_000,
            output_dir=Path(directory),
        )
        print(evidence.metrics)
        if evidence.metrics["execution_engine"] != "NautilusTrader":
            raise AssertionError("wrong execution engine")
        if evidence.metrics["custom_fill_simulator"] is not False:
            raise AssertionError("custom fill simulator flag")
        if evidence.metrics["submissions"] != 1:
            raise AssertionError(f"expected one submission: {evidence.metrics}")
        if evidence.metrics["closed_positions"] != 1:
            raise AssertionError(f"expected one closed position: {evidence.metrics}")
        if not evidence.metrics["ended_flat"]:
            raise AssertionError(f"smoke did not end flat: {evidence.metrics}")
    print("authoritative TradeTick order-path smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
