#!/usr/bin/env python3
"""Non-performance smoke for the authoritative TradeTick limit-bracket path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from aggtrade_data import AggTrade
from core import Side
from impact_regime_probe import ScenarioPlan
from nautilus_plan_backtest import NautilusExecutionConfig
from nautilus_tick_limit_plan_backtest import (
    RestingEntryInstruction,
    run_nautilus_tick_limit_plan_backtest,
)


def main() -> int:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=8)
    start_ns = int(start.timestamp() * 1_000_000_000)
    prices = (
        100.5,
        100.8,
        101.0,
        100.7,
        100.0,
        101.5,
        104.2,
        104.0,
        103.9,
        103.8,
        103.7,
    )
    trades = [
        AggTrade(
            agg_trade_id=index + 1,
            price=price,
            quantity=10.0,
            first_trade_id=index + 1,
            last_trade_id=index + 1,
            ts_event_ns=start_ns + index * 1_000_000_000,
            # The boundary retest at index 4 is seller-aggressive so a resting
            # buy limit can fill. The later target trade remains buyer-aggressive
            # so the protective take-profit sell can fill.
            is_buyer_maker=(index == 4),
        )
        for index, price in enumerate(prices)
    ]
    plan = ScenarioPlan(
        scenario_id="tick-limit-smoke-long",
        response="CONTINUATION",
        side=Side.LONG,
        signal_bar_index=0,
        signal_time_ns=start_ns + 1_000_000_000,
        stop_price=98.0,
        target_price=104.0,
        confirmation_hold_price=100.0,
        structure_high=104.0,
        structure_low=98.0,
        structure_midpoint=101.0,
        pulse_high=101.0,
        pulse_low=99.5,
        pulse_flow_score=1.0,
        pulse_move_atr=1.0,
        pulse_path_efficiency=1.0,
        pulse_close_location=1.0,
        reason_code="TRADETICK_LIMIT_ADAPTER_SMOKE",
    )
    instruction = RestingEntryInstruction(
        plan=plan,
        entry_price=100.0,
        expiry_time_ns=start_ns + 6_000_000_000,
        entry_reason="CONFIRMATION_BOUNDARY_RETEST_SMOKE",
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
    with tempfile.TemporaryDirectory(
        prefix="candidate01-tick-limit-smoke-",
    ) as directory:
        evidence = run_nautilus_tick_limit_plan_backtest(
            label="tick-limit-smoke",
            trades=trades,
            instructions=[instruction],
            evaluation_start=start,
            evaluation_end=end,
            execution=execution,
            maximum_hold_ns=5_000_000_000,
            output_dir=Path(directory),
        )
        metrics = evidence.metrics
        print(metrics)
        if metrics["execution_engine"] != "NautilusTrader":
            raise AssertionError("wrong execution engine")
        if metrics["custom_fill_simulator"] is not False:
            raise AssertionError("custom fill simulator flag")
        if metrics["entry_order_type"] != "LIMIT":
            raise AssertionError(f"wrong entry type: {metrics}")
        if metrics["submissions"] != 1:
            raise AssertionError(f"expected one submission: {metrics}")
        if metrics["closed_positions"] != 1:
            raise AssertionError(f"expected one closed position: {metrics}")
        if metrics["limit_entries_expired"] != 0:
            raise AssertionError(f"unexpected expiry: {metrics}")
        if metrics["targets_consumed_before_entry"] != 0:
            raise AssertionError(f"unexpected target cancel: {metrics}")
        if not metrics["ended_flat"] or not metrics["ended_without_pending_entry"]:
            raise AssertionError(f"smoke did not end cleanly: {metrics}")
    print("authoritative TradeTick limit-bracket smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
