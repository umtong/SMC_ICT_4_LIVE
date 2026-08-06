#!/usr/bin/env python3
"""Replay frozen V18c and annotate only past-known regime state.

Signal detection, target selection, stops, risk sizing and NautilusTrader order
submission are unchanged. The additional fields are calculated from completed
bars before the entry event and exist solely to distinguish market-state logic
from implementation errors across the first two BTC weeks.
"""
from __future__ import annotations

import math
from typing import Any

import nt_backtest as base
import nt_liquidity_strategy as core
from nt_auction_excess_strategy import weighted_location
from nt_causal_risk_sizing import risk_sized_submit_bracket


HORIZONS = (15, 30, 60, 120, 240, 480, 720)
FAIR_SLOPE_HORIZONS = (15, 30, 60)
NANOS_PER_MINUTE = 60_000_000_000
SESSION_MINUTES = 8 * 60


def _fair_value(rows: list[dict[str, float | int]], window: int) -> float:
    if len(rows) < window:
        return float("nan")
    selected = rows[-window:]
    typical = [
        (float(item["high"]) + float(item["low"]) + float(item["close"])) / 3.0
        for item in selected
    ]
    volume = [float(item["volume"]) for item in selected]
    mean, _ = weighted_location(typical, volume)
    return float(mean)


def _regime_details(self: Any, side: int, details: dict[str, Any]) -> dict[str, float]:
    rows = list(self.bars)
    atr = float(self._atr())
    result: dict[str, float] = {}
    if not rows or not math.isfinite(atr) or atr <= 0.0:
        return result
    close = float(rows[-1]["close"])
    for horizon in HORIZONS:
        if len(rows) < horizon + 1:
            continue
        previous_close = float(rows[-horizon - 1]["close"])
        selected = rows[-(horizon + 1) :]
        high = max(float(item["high"]) for item in selected)
        low = min(float(item["low"]) for item in selected)
        path = sum(
            abs(float(current["close"]) - float(previous["close"]))
            for previous, current in zip(selected, selected[1:])
        )
        span = max(high - low, 1e-12)
        result[f"directional_displacement_{horizon}m_atr"] = (
            side * (close - previous_close) / atr
        )
        result[f"auction_efficiency_{horizon}m_context"] = (
            abs(close - previous_close) / path if path > 0.0 else 0.0
        )
        result[f"directional_range_location_{horizon}m"] = (
            (close - low) / span if side > 0 else (high - close) / span
        )
        result[f"path_{horizon}m_atr"] = path / atr
        result[f"range_{horizon}m_atr"] = span / atr

    value_window = int(details.get("scale_value_window", 240))
    current_fair = _fair_value(rows[:-1], value_window)
    for horizon in FAIR_SLOPE_HORIZONS:
        older_rows = rows[: -(horizon + 1)] if len(rows) > horizon + 1 else []
        older_fair = _fair_value(older_rows, value_window)
        if math.isfinite(current_fair) and math.isfinite(older_fair):
            result[f"directional_fair_value_slope_{horizon}m_atr"] = (
                side * (current_fair - older_fair) / atr
            )

    fair = float(details.get("fair_value", float("nan")))
    dispersion = float(details.get("dispersion", float("nan")))
    if math.isfinite(fair):
        result["directional_distance_from_fair_atr"] = side * (close - fair) / atr
    if math.isfinite(fair) and math.isfinite(dispersion) and dispersion > 0.0:
        result["directional_distance_from_fair_sigma"] = side * (close - fair) / dispersion

    ts_event = int(rows[-1]["ts"])
    absolute_minute = ts_event // NANOS_PER_MINUTE
    result["utc_hour_fraction"] = (absolute_minute % (24 * 60)) / 60.0
    result["funding_session_offset_minutes"] = float(absolute_minute % SESSION_MINUTES)
    return result


def _diagnostic_submit(
    self: Any,
    setup: core.PendingSetup,
    row: dict[str, float | int],
    target_net_r: float,
    details: dict[str, Any],
) -> bool:
    annotated = {
        **details,
        **_regime_details(self, int(setup.side), details),
        "regime_diagnostic_only": True,
    }
    return risk_sized_submit_bracket(
        self,
        setup,
        row,
        target_net_r,
        annotated,
    )


core.LiquidityTransitionStrategy._submit_bracket = _diagnostic_submit

_original_importable_strategy_config = base.ImportableStrategyConfig


def _strategy_config(*args: Any, **kwargs: Any) -> Any:
    if kwargs.get("strategy_path") == "nt_liquidity_strategy:LiquidityTransitionStrategy":
        kwargs["strategy_path"] = (
            "nt_composite_liquidity_strategy:CompositeLiquidityRouterStrategy"
        )
    return _original_importable_strategy_config(*args, **kwargs)


base.ImportableStrategyConfig = _strategy_config


if __name__ == "__main__":
    base.main()
