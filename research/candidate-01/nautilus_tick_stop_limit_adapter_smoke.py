#!/usr/bin/env python3
"""Non-performance smoke for the generated authoritative STOP_LIMIT adapter."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

from aggtrade_data import AggTrade
from core import Side
from impact_regime_probe import ScenarioPlan
from nautilus_plan_backtest import NautilusExecutionConfig
from nautilus_tick_stop_plan_backtest import (
    StopEntryInstruction,
    run_nautilus_tick_stop_plan_backtest,
)


def main() -> int:
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(seconds=9)
    start_ns = int(start.timestamp() * 1_000_000_000)
    prices = (
        100.0,
        100.2,
        100.5,
        100.9,
        101.1,
        101.0,
        101.2,
        102.0,
        103.1,
        103.0,
        102.9,
        102.8,
    )
    buyer_maker = (
        False,
        False,
        False,
        False,
        False,
        True,
        False,
        False,
        False,
        True,
        True,
        True,
    )
    trades = [
        AggTrade(
            agg_trade_id=index + 1,
            price=price,
            quantity=10.0,
            first_trade_id=index + 1,
            last_trade_id=index + 1,
            ts_event_ns=start_ns + index * 1_000_000_000,
            is_buyer_maker=buyer_maker[index],
        )
        for index, price in enumerate(prices)
    ]
    plan = ScenarioPlan(
        scenario_id="tick-stop-limit-smoke-long",
        response="CONTINUATION",
        side=Side.LONG,
        signal_bar_index=0,
        signal_time_ns=start_ns + 1_000_000_000,
        stop_price=99.0,
        target_price=103.0,
        confirmation_hold_price=99.9,
        structure_high=103.0,
        structure_low=99.0,
        structure_midpoint=101.0,
        pulse_high=101.0,
        pulse_low=99.5,
        pulse_flow_score=1.0,
        pulse_move_atr=1.0,
        pulse_path_efficiency=1.0,
        pulse_close_location=1.0,
        reason_code="TRADETICK_STOP_LIMIT_ADAPTER_SMOKE",
    )
    instruction = StopEntryInstruction(
        plan=plan,
        trigger_price=101.0,
        limit_price=101.1,
        expiry_time_ns=start_ns + 7_000_000_000,
        entry_reason="CAUSAL_RESUMPTION_STOP_LIMIT_SMOKE",
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
        prefix="candidate01-stop-limit-adapter-smoke-",
    ) as directory:
        evidence = run_nautilus_tick_stop_plan_backtest(
            label="tick-stop-limit-adapter-smoke",
            trades=trades,
            instructions=[instruction],
            evaluation_start=start,
            evaluation_end=end,
            execution=execution,
            maximum_hold_ns=6_000_000_000,
            output_dir=Path(directory),
        )
        metrics = evidence.metrics
        print(metrics)
        if metrics["execution_engine"] != "NautilusTrader":
            raise AssertionError("wrong execution engine")
        if metrics["custom_fill_simulator"] is not False:
            raise AssertionError("custom fill simulator flag")
        if metrics["entry_order_type"] != "STOP_LIMIT":
            raise AssertionError(f"wrong entry type: {metrics}")
        if metrics["submissions"] != 1:
            raise AssertionError(f"expected one submission: {metrics}")
        if metrics["closed_positions"] != 1:
            raise AssertionError(f"expected one closed position: {metrics}")
        if metrics["stop_entries_expired"] != 0:
            raise AssertionError(f"unexpected expiry: {metrics}")
        if metrics["targets_consumed_before_entry"] != 0:
            raise AssertionError(f"unexpected target cancel: {metrics}")
        if metrics["invalidations_before_entry"] != 0:
            raise AssertionError(f"unexpected invalidation cancel: {metrics}")
        if metrics["one_global_entry_gate_violations"] != 0:
            raise AssertionError(f"global gate violation: {metrics}")
        if metrics["protective_order_failures"] != 0:
            raise AssertionError(f"protective failure: {metrics}")
        if not metrics["ended_flat"] or not metrics["ended_without_pending_entry"]:
            raise AssertionError(f"smoke did not end cleanly: {metrics}")
        submission = evidence.submissions[0]
        if submission["planned_limit_price"] < submission["planned_trigger_price"]:
            raise AssertionError(f"long cap below trigger: {submission}")
        print("authoritative TradeTick STOP_LIMIT adapter smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
