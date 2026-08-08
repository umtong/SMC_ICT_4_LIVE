#!/usr/bin/env python3
"""Synthetic causal regression for Candidate-02 V153/V154.

This verifies ordering and geometry only. It makes no performance claim.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

from logic import BarObs, LogicConfig, MINUTE_NS
from quarter_hour_common_flow import QH_MODULE, QuarterHourCommonFlowEngine


def main() -> None:
    config = LogicConfig(
        atr_period=10,
        displacement_body_atr=0.20,
        displacement_flow_min=0.03,
        reacceleration_body_atr=0.18,
        reacceleration_flow_min=0.04,
        min_net_r=0.50,
    )
    engine = QuarterHourCommonFlowEngine(config)
    start = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1e9)
    symbols = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
    prices = {symbol: 100.0 + index * 20.0 for index, symbol in enumerate(symbols)}
    generated = []

    def batch(minute: int, moves: Mapping[str, float], *, flow: float = 0.70) -> None:
        bars = {}
        for symbol in symbols:
            open_price = prices[symbol]
            move = float(moves.get(symbol, 0.0))
            close = open_price + move
            high = max(open_price, close) + 0.02
            low = min(open_price, close) - 0.02
            volume = 1_000.0
            taker = (
                volume * flow
                if move > 0
                else volume * (1.0 - flow)
                if move < 0
                else volume * 0.5
            )
            bars[symbol] = BarObs(
                start + minute * MINUTE_NS,
                open_price,
                high,
                low,
                close,
                volume,
                taker,
            )
            prices[symbol] = close
        generated.extend(engine.on_batch(start + minute * MINUTE_NS, bars))

    for minute in range(1, 31):
        batch(minute, {})

    # Three aligned markets establish context at the first five minutes of a
    # UTC quarter hour. The owner is BTC and must not be chased.
    for minute in range(31, 36):
        batch(
            minute,
            {"BTCUSDT": 0.20, "ETHUSDT": 0.18, "SOLUSDT": 0.16},
        )
    assert engine._episodes, engine.skips
    assert not generated, "initiative context emitted an entry"
    assert "BTCUSDT" not in engine._episodes, "information owner was chased"

    # Followers deliver beyond their initiative and leave the first FVG.
    batch(36, {"BTCUSDT": 0.30, "ETHUSDT": 0.28, "SOLUSDT": 0.26})
    batch(37, {"BTCUSDT": 0.08, "ETHUSDT": 0.08, "SOLUSDT": 0.08})
    batch(38, {"BTCUSDT": 0.35, "ETHUSDT": 0.32, "SOLUSDT": 0.30})
    batch(39, {"BTCUSDT": 0.12, "ETHUSDT": 0.12, "SOLUSDT": 0.12})

    # The first FVG is actually retraced and held outside the initiative.
    batch(
        40,
        {"BTCUSDT": -0.50, "ETHUSDT": -0.47, "SOLUSDT": -0.44},
        flow=0.30,
    )
    batch(41, {"BTCUSDT": 0.03, "ETHUSDT": 0.03, "SOLUSDT": 0.03})

    # A separate three-bar reacceleration leg leaves a fresh FVG.
    batch(42, {"BTCUSDT": 0.30, "ETHUSDT": 0.28, "SOLUSDT": 0.26})
    batch(43, {"BTCUSDT": 0.12, "ETHUSDT": 0.12, "SOLUSDT": 0.12})
    batch(44, {"BTCUSDT": 0.36, "ETHUSDT": 0.34, "SOLUSDT": 0.32})

    assert generated, engine.skips
    episode_keys = set()
    for symbol, plan in generated:
        details = plan.details
        assert symbol in {"ETHUSDT", "SOLUSDT"}
        assert details["module"] == QH_MODULE
        assert details["route"] == "COMMON_FLOW_THEN_COMPLETED_AUCTION_LEG"
        assert details["entry_model"] == "PASSIVE_FRESH_REACCELERATION_FVG_MIDPOINT"
        assert plan.entry_order_type == "LIMIT" and plan.entry_post_only
        assert details["initiative_end_ts_ns"] < details["fvg_retrace_ts_ns"]
        assert details["fvg_retrace_ts_ns"] < details["fresh_reacceleration_fvg_ts_ns"]
        assert details["fresh_reacceleration_fvg_ts_ns"] <= plan.observed_ts_ns
        key = details["independent_episode_key"]
        assert key not in episode_keys
        episode_keys.add(key)
    print(f"v154 synthetic causal state test: OK ({len(generated)} plans)")


if __name__ == "__main__":
    main()
