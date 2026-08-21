#!/usr/bin/env python3
"""Fast structural checks before spending compute on market data."""
from __future__ import annotations

from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

from directional_context import build_directional_snapshot
from nautilus_strategy import BracketLifecycle, LifecycleState, TradePlan
from risk_sizing import size_three_percent_risk
from route_directional_policy import assign_market_event_clusters, route_account


def _bars(rows: int = 900) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=rows, freq="min", tz="UTC")
    wave = np.sin(np.linspace(0.0, 16.0, rows))
    trend = np.linspace(0.0, 14.0, rows)
    close = 100.0 + trend + wave
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.20
    low = np.minimum(open_, close) - 0.20
    quote = np.full(rows, 1_000_000.0)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "quote_volume": quote,
        },
        index=index,
    )


def check_causal_direction() -> None:
    bars = _bars()
    decision = 700
    first = build_directional_snapshot(bars, decision, "LONG", 1.0)
    changed = bars.copy()
    changed.iloc[decision + 1 :, changed.columns.get_loc("close")] += 10_000.0
    changed.iloc[decision + 1 :, changed.columns.get_loc("high")] += 10_000.0
    second = build_directional_snapshot(changed, decision, "LONG", 1.0)
    assert first == second, "future bars changed a decision-time state"
    short = build_directional_snapshot(bars, decision, "SHORT", 1.0)
    assert first.trend_alignment == -short.trend_alignment

    expanded = bars.copy()
    expanded.loc[expanded.index[decision - 60 : decision + 1], "quote_volume"] *= 4.0
    active = build_directional_snapshot(expanded, decision, "LONG", 1.0)
    assert active.activity_ratio > 3.5, "volume expansion was silently capped at one"


def check_risk_and_lifecycle() -> None:
    sizing = size_three_percent_risk(
        nav=100_000.0,
        entry=100.0,
        stop=99.5,
        tick_size=0.01,
        quantity_step=0.001,
    )
    assert 0.0299 <= sizing.planned_risk_fraction <= 0.03
    assert 5.0 < sizing.required_leverage < 6.5
    plan = TradePlan("p", "BTCUSDT-PERP.BINANCE", "LONG", 100.0, 99.0, 101.2)
    assert plan.plan_id == "p"
    life = BracketLifecycle()
    life.arm("p", "entry")
    life.protect("stop", "target")
    sibling = life.close("target")
    assert sibling == "stop" and life.state == LifecycleState.CLOSED


def _ns(timestamp: str) -> int:
    return int(pd.Timestamp(timestamp, tz="UTC").value)


def check_market_event_and_account_router() -> None:
    rows = [
        # Same causal event, same minute: ETH wins causal arbitration.
        dict(symbol="BTCUSDT", side="LONG", family="FAILED_AUCTION_REVERSAL", episode_id="a", interaction_time_ns=_ns("2025-01-01 00:00"), order_time_ns=_ns("2025-01-01 00:03"), order_terminal_time_ns=_ns("2025-01-01 00:12"), resolution_time_ns=_ns("2025-01-01 00:08"), opportunity_score=1.1, gross_rr=1.4, route_strength=2.0, mechanism_coherence=0.7, outcome="TARGET_FIRST", net_r=1.2, entry=100.0, stop=99.0, holding_minutes=5),
        dict(symbol="ETHUSDT", side="LONG", family="FAILED_AUCTION_REVERSAL", episode_id="b", interaction_time_ns=_ns("2025-01-01 00:02"), order_time_ns=_ns("2025-01-01 00:03"), order_terminal_time_ns=_ns("2025-01-01 00:10"), resolution_time_ns=_ns("2025-01-01 00:07"), opportunity_score=1.5, gross_rr=1.6, route_strength=2.0, mechanism_coherence=0.9, outcome="TARGET_FIRST", net_r=1.4, entry=200.0, stop=198.0, holding_minutes=4),
        # Later observation of the same broad event cannot inflate trades.
        dict(symbol="SOLUSDT", side="LONG", family="INITIATIVE_MITIGATION_CONTINUATION", episode_id="c", interaction_time_ns=_ns("2025-01-01 00:05"), order_time_ns=_ns("2025-01-01 00:06"), order_terminal_time_ns=_ns("2025-01-01 00:09"), resolution_time_ns=_ns("2025-01-01 00:09"), opportunity_score=2.0, gross_rr=2.0, route_strength=2.0, mechanism_coherence=1.0, outcome="TARGET_FIRST", net_r=1.8, entry=50.0, stop=49.0, holding_minutes=3),
        # Independent event after the account is free.
        dict(symbol="XRPUSDT", side="SHORT", family="ACCEPTED_AUCTION_CONTINUATION", episode_id="d", interaction_time_ns=_ns("2025-01-01 00:20"), order_time_ns=_ns("2025-01-01 00:22"), order_terminal_time_ns=_ns("2025-01-01 00:30"), resolution_time_ns=_ns("2025-01-01 00:27"), opportunity_score=1.2, gross_rr=1.3, route_strength=2.0, mechanism_coherence=0.8, outcome="STOP_FIRST", net_r=-1.0, entry=10.0, stop=10.2, holding_minutes=5),
    ]
    frame = pd.DataFrame(rows)
    clustered = assign_market_event_clusters(frame)
    assert clustered.iloc[0].market_event_id == clustered.iloc[1].market_event_id
    assert clustered.iloc[1].market_event_id == clustered.iloc[2].market_event_id
    assert clustered.iloc[3].market_event_id != clustered.iloc[2].market_event_id
    selected, closed, _, account = route_account(frame)
    assert selected.episode_id.tolist() == ["b", "d"]
    assert len(closed) == 2
    expected = (1.0 + 0.03 * 1.4) * (1.0 - 0.03)
    assert abs(account["ending_nav_multiplier"] - expected) < 1e-12


def check_source_contract() -> None:
    root = Path(__file__).resolve().parent
    policy = (root / "directional_liquidity_policy.py").read_text(encoding="utf-8")
    router = (root / "route_directional_policy.py").read_text(encoding="utf-8")
    combined = policy + router
    assert "symbol_index" not in combined
    assert "sklearn" not in combined
    assert "class_weight" not in combined
    assert "force_time" not in combined.lower()
    assert "right_low <= left_high" in policy
    assert "right_high >= left_low" in policy
    assert "gross_rr < 1.0" in policy
    assert "one_plan" in policy.lower()


def main() -> None:
    check_causal_direction()
    check_risk_and_lifecycle()
    check_market_event_and_account_router()
    check_source_contract()
    print("directional-liquidity-policy-v2 self-check: OK")


if __name__ == "__main__":
    main()
