#!/usr/bin/env python3
"""Deterministic contracts for CIRB parent-frozen five-second resolution."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pandas as pd

from cirb_execution_resolution import (
    FrozenParent,
    build_child_plans,
    freeze_parent_events,
)
from futures_metrics_data import FuturesMetric

NS = 1_000_000_000


def _frame(parent_ts: int, rows: list[dict[str, float]]) -> pd.DataFrame:
    materialized = []
    for index, row in enumerate(rows, start=1):
        item = {
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row.get("volume", 10.0),
            "taker_buy_volume": row.get("taker_buy_volume", 5.0),
            "trades": int(row.get("trades", 1)),
        }
        materialized.append(item)
    index = pd.to_datetime(
        [parent_ts + i * 5 * NS for i in range(1, len(materialized) + 1)],
        unit="ns",
        utc=True,
    )
    return pd.DataFrame(materialized, index=index)


def _parent(*, branch: str = "DISCHARGE", side: str = "SELL") -> FrozenParent:
    return FrozenParent(
        scenario_id="CIRB-1000000000000-000001",
        observed_ts_ns=1_000_000_000_000,
        side=side,
        crowding_branch=branch,
        event_drop=0.01,
        drop_threshold=0.005,
        event_open=101.0,
        event_high=102.0,
        event_low=98.0,
        event_close=99.0,
        event_mid=100.0,
        event_range=4.0,
        atr=2.0,
        slow_mid=104.0,
        upper_fast=106.0,
        lower_fast=94.0,
        prior_open_interest=101.0,
        event_open_interest=100.0,
        prior_all_account_ratio=1.0,
        event_all_account_ratio=(1.01 if branch == "COUNTER_INVENTORY" and side == "SELL" else (0.99 if side == "SELL" else 1.01)),
        shock_sign=-1.0 if side == "SELL" else 1.0,
        baseline_entry={
            "reference_price": 101.0,
            "scenario_id": "baseline:ENTRY",
        },
        baseline_rr_eroded=True,
    )


def _logic() -> dict[str, float | int | bool]:
    return {
        "oidb_response_flow_ratio": 0.05,
        "oidb_reclaim_close_location": 0.58,
        "minimum_structural_rr": 0.75,
        "oidb_stop_buffer_atr": 0.08,
        "oidb_extension_atr": 0.05,
        "oidb_projection_fraction": 1.0,
        "oidb_persistence_fraction": 0.35,
        "oidb_response_bars": 6,
        "cirb_counter_response_bars": 15,
        "cirb_counter_rebuild_fraction": 0.35,
        "cirb_require_counter_composition_persistence": True,
        "oidb_invalidation_observation_bars": 6,
    }


def test_authoritative_parent_ledger_freezes_exact_identity() -> None:
    timestamps = pd.date_range(
        "2024-01-01T00:01:00Z",
        periods=121,
        freq="1min",
    )
    rows = []
    for index in range(121):
        open_ = 100.0 + (index % 3) * 0.1
        close = open_ + (0.1 if index % 2 == 0 else -0.1)
        rows.append(
            {
                "open": open_,
                "high": max(open_, close) + 0.2,
                "low": min(open_, close) - 0.2,
                "close": close,
                "volume": 10.0,
                "taker_buy_volume": 5.0,
                "trades": 10,
            }
        )
    frame = pd.DataFrame(rows, index=timestamps)
    parent_ts = int(timestamps[-1].value)
    last = frame.iloc[-1]
    parent_id = f"CIRB-{parent_ts}-000001"
    events = [
        {
            "event_type": "CROWDING_INVENTORY_RESPONSE_TRANSITION",
            "previous_state": "IDLE",
            "next_state": "DELEVERAGING_WAVE_OBSERVED",
            "observed_time_ns": parent_ts,
            "scenario_id": parent_id,
            "details": {
                "forced_side": "SELL",
                "crowding_branch": "DISCHARGE",
                "event_open": float(last["open"]),
                "event_high": float(last["high"]),
                "event_low": float(last["low"]),
                "event_close": float(last["close"]),
                "open_interest_drop_fraction": 0.01,
                "prior_only_drop_threshold": 0.005,
                "prior_open_interest": 101.0,
                "event_open_interest": 100.0,
                "prior_all_account_ratio": 1.0,
                "event_all_account_ratio": 0.99,
                "shock_sign": -1.0,
            },
        },
        {
            "event_type": "CIRB_ENTRY_TRANSITION",
            "next_state": "ENTRY_ARMED",
            "observed_time_ns": parent_ts + 60 * NS,
            "scenario_id": f"{parent_id}:ENTRY",
            "reason_code": "CROWD_DISCHARGE_REVERSAL_ENTRY_ARMED",
            "reference_price": "100.0",
            "details": {
                "context_scenario_id": parent_id,
                "stop": 98.0,
                "target": 104.0,
                "target_reason": "PRE_SHOCK_DEALING_RANGE_EQUILIBRIUM",
            },
        },
        {
            "event_type": "SCENARIO_TRANSITION",
            "reason_code": "NET_REWARD_RISK_ERODED_AFTER_DELAY",
            "scenario_id": f"{parent_id}:ENTRY",
        },
    ]
    metric = FuturesMetric(
        ts_ns=parent_ts,
        open_interest=100.0,
        open_interest_value=1.0,
        top_account_long_short=1.0,
        top_position_long_short=1.0,
        all_account_long_short=0.99,
        taker_buy_sell_ratio=0.5,
    )
    params = {
        "fast_lookback": 20,
        "slow_lookback": 120,
        "atr_lookback": 20,
        "volume_lookback": 60,
        "pool_tolerance_atr": 0.12,
    }
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "events.jsonl"
        path.write_text(
            "\n".join(json.dumps(item) for item in events) + "\n",
            encoding="utf-8",
        )
        parents, audit = freeze_parent_events(
            baseline_events_path=path,
            one_minute_frame=frame,
            metrics={parent_ts: metric},
            logic_params=params,
        )
    assert len(parents) == 1
    assert parents[0].scenario_id == parent_id
    assert parents[0].baseline_rr_eroded is True
    assert audit["semantic_drift_count"] == 0
    assert len(audit["parent_event_identity_hash"]) == 64
    assert len(audit["baseline_entry_identity_hash"]) == 64


def test_event_bar_cannot_trade_and_later_discharge_reversal_can() -> None:
    parent = _parent()
    frame = _frame(
        parent.observed_ts_ns,
        [
            {
                "open": 99.0,
                "high": 100.6,
                "low": 98.8,
                "close": 100.5,
                "volume": 10.0,
                "taker_buy_volume": 8.0,
            }
        ],
    )
    plans, diagnostics = build_child_plans(
        parents=(parent,),
        five_second_frame=frame,
        metrics={},
        logic_params=_logic(),
        enable_discharge=True,
        enable_counter_inventory=True,
    )
    assert len(plans) == 1
    plan = plans[0]
    assert plan.signal.observed_ts_ns > parent.observed_ts_ns
    assert plan.signal.direction == "LONG"
    assert plan.signal.family == "CIRB_D_R"
    assert plan.baseline_rr_eroded is True
    assert diagnostics["rescued_by_5s_candidate_count"] == 1


def test_counter_inventory_cannot_reverse() -> None:
    parent = _parent(branch="COUNTER_INVENTORY")
    frame = _frame(
        parent.observed_ts_ns,
        [
            {
                "open": 99.0,
                "high": 101.0,
                "low": 98.5,
                "close": 100.8,
                "volume": 10.0,
                "taker_buy_volume": 9.0,
            }
        ],
    )
    plans, diagnostics = build_child_plans(
        parents=(parent,),
        five_second_frame=frame,
        metrics={},
        logic_params=_logic(),
        enable_discharge=True,
        enable_counter_inventory=True,
    )
    assert plans == ()
    assert diagnostics["response_outcomes"][
        "COUNTER_INVENTORY_INVALIDATED_BY_OPPOSITE_RECLAIM"
    ] == 1


def test_counter_inventory_requires_later_completed_rebuild() -> None:
    parent = _parent(branch="COUNTER_INVENTORY")
    # The first 59 bars do not qualify. The 60th completes five minutes later,
    # where a new metrics observation may causally confirm rebuild and trapping.
    rows = [
        {
            "open": 99.0,
            "high": 99.1,
            "low": 98.9,
            "close": 99.0,
            "volume": 10.0,
            "taker_buy_volume": 5.0,
        }
        for _ in range(59)
    ]
    rows.append(
        {
            "open": 98.0,
            "high": 98.1,
            "low": 96.0,
            "close": 96.2,
            "volume": 10.0,
            "taker_buy_volume": 1.0,
        }
    )
    frame = _frame(parent.observed_ts_ns, rows)
    metric_ts = parent.observed_ts_ns + 300 * NS
    metrics = {
        metric_ts: FuturesMetric(
            ts_ns=metric_ts,
            open_interest=101.0,
            open_interest_value=1.0,
            top_account_long_short=1.0,
            top_position_long_short=1.0,
            all_account_long_short=1.02,
            taker_buy_sell_ratio=0.5,
        )
    }
    plans, _ = build_child_plans(
        parents=(parent,),
        five_second_frame=frame,
        metrics=metrics,
        logic_params=_logic(),
        enable_discharge=True,
        enable_counter_inventory=True,
    )
    assert len(plans) == 1
    assert plans[0].signal.observed_ts_ns == metric_ts
    assert plans[0].signal.direction == "SHORT"
    assert plans[0].signal.family == "CIRB_T_C"


def test_discharge_only_is_a_true_branch_ablation() -> None:
    parent = _parent(branch="COUNTER_INVENTORY")
    frame = _frame(
        parent.observed_ts_ns,
        [
            {
                "open": 99.0,
                "high": 99.0,
                "low": 98.0,
                "close": 98.1,
                "volume": 10.0,
                "taker_buy_volume": 1.0,
            }
        ],
    )
    plans, diagnostics = build_child_plans(
        parents=(parent,),
        five_second_frame=frame,
        metrics={},
        logic_params=_logic(),
        enable_discharge=True,
        enable_counter_inventory=False,
    )
    assert plans == ()
    assert diagnostics["response_outcomes"]["COUNTER_INVENTORY_DISABLED"] == 1


if __name__ == "__main__":
    test_authoritative_parent_ledger_freezes_exact_identity()
    test_event_bar_cannot_trade_and_later_discharge_reversal_can()
    test_counter_inventory_cannot_reverse()
    test_counter_inventory_requires_later_completed_rebuild()
    test_discharge_only_is_a_true_branch_ablation()
    print("CIRB execution-resolution contracts passed")
