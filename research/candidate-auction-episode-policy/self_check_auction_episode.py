#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import replace
import math

import pandas as pd

from auction_episode_research import ActionSpec, label_action


def _data(rows):
    index = pd.date_range("2025-01-01T00:01:00Z", periods=len(rows), freq="1min")
    return pd.DataFrame(rows, index=index)


def _action(**changes):
    base = ActionSpec(
        action_id="a",
        episode_id="e",
        symbol="BTCUSDT",
        event_type="FAILED_AUCTION",
        decision_stage="RECLAIM_CONTROL_TRANSFER",
        side="LONG",
        emission_index=0,
        emission_time_ns=0,
        entry_style="BOUNDARY_LIMIT",
        entry=100.0,
        stop=99.0,
        target=102.0,
        entry_expiry_minutes=3,
        source_level_id="s",
        source_kind="15M_PIVOT_LOW",
        source_timeframe_minutes=15,
        source_span=2,
        source_price=100.0,
        source_lower=99.9,
        source_upper=100.1,
        source_strength_ratio=2.0,
        source_defense_count=2,
        source_age_minutes=30.0,
        objective_id="t",
        objective_kind="15M_PIVOT_HIGH",
        objective_timeframe_minutes=15,
        objective_strength_ratio=2.0,
        interaction_time_ns=0,
        feature_values={},
    )
    return replace(base, **changes)


def main() -> None:
    tick = 0.1
    winning = _data(
        [
            {"open": 101.0, "high": 101.2, "low": 100.7, "close": 101.0},
            {"open": 100.8, "high": 100.9, "low": 99.8, "close": 100.4},
            {"open": 100.4, "high": 101.0, "low": 100.2, "close": 100.9},
            {"open": 100.9, "high": 102.1, "low": 100.8, "close": 102.0},
        ],
    )
    result = label_action(winning, _action(), tick)
    assert result.fill_state == "FILLED_LIMIT", result
    assert result.outcome == "TARGET_FIRST", result
    assert result.net_r is not None and result.net_r > 0.0

    no_trade_through = _data(
        [
            {"open": 101.0, "high": 101.2, "low": 100.7, "close": 101.0},
            {"open": 100.8, "high": 101.0, "low": 100.0, "close": 100.6},
            {"open": 100.6, "high": 101.1, "low": 100.0, "close": 100.8},
            {"open": 100.8, "high": 101.2, "low": 100.1, "close": 101.0},
        ],
    )
    result = label_action(no_trade_through, _action(), tick)
    assert result.outcome == "UNFILLED", result

    stopped_on_fill = _data(
        [
            {"open": 101.0, "high": 101.2, "low": 100.7, "close": 101.0},
            {"open": 100.8, "high": 102.3, "low": 98.8, "close": 101.5},
            {"open": 101.5, "high": 101.6, "low": 101.0, "close": 101.2},
        ],
    )
    result = label_action(stopped_on_fill, _action(), tick)
    assert result.outcome == "STOP_FIRST", result
    assert result.net_r is not None and result.net_r < -1.0

    market = _action(entry_style="MARKET")
    result = label_action(winning, market, tick)
    assert result.fill_state == "FILLED_MARKET"
    assert math.isfinite(result.target_net_r)
    print("auction episode self-check ok")


if __name__ == "__main__":
    main()
