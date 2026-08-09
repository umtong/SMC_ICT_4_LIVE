"""Structural-risk adaptation of Candidate 47's public ichiV2 policy.

The external policy uses a 10% emergency stop while normally exiting on a
five-minute close crossing the shifted 90-minute EMA.  That emergency stop is
not the causal invalidation of the entry and makes current-NAV 3% risk sizing
far too small.  This variant changes no entry or dynamic-exit condition.  It
places the hard stop below all three causal supports known at decision time:

* the completed signal bar low;
* the shifted 90-minute EMA used by the source exit;
* the upper boundary of the shifted Ichimoku cloud.

The original 10% stop remains the maximum possible distance.  Fees, adverse
slippage and funding reserve remain inside per-unit planned loss, and
NautilusTrader remains the only execution/account engine.
"""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ichifan_strategy as _exact
import router as _router

Candidate47IchiFanStructuralConfig = _exact.Candidate47IchiFanConfig
Candidate35Config = Candidate47IchiFanStructuralConfig
SYMBOLS = _exact.SYMBOLS


def causal_structural_stop(
    *,
    entry: float,
    signal_bar_low: float,
    trend_close_90m: float,
    cloud_a: float,
    cloud_b: float,
    emergency_fraction: float = 0.10,
) -> tuple[float, dict[str, float]]:
    """Return a long hard stop below every completed-bar causal support.

    The function never widens the source policy's 10% emergency stop.  It is
    deliberately parameter-free apart from that inherited source value.
    """
    values = (
        float(entry),
        float(signal_bar_low),
        float(trend_close_90m),
        float(cloud_a),
        float(cloud_b),
    )
    if not all(math.isfinite(value) and value > 0.0 for value in values):
        raise ValueError("structural stop requires finite positive causal inputs")
    if not (0.0 < emergency_fraction < 1.0):
        raise ValueError("emergency_fraction must be inside (0, 1)")

    cloud_top = max(float(cloud_a), float(cloud_b))
    causal_floor = min(
        float(signal_bar_low),
        float(trend_close_90m),
        cloud_top,
    )
    emergency_stop = float(entry) * (1.0 - emergency_fraction)
    stop = max(emergency_stop, causal_floor)
    if not stop < entry:
        raise ValueError(f"invalid long structural stop {stop} >= entry {entry}")
    return stop, {
        "signal_bar_low": float(signal_bar_low),
        "trend_close_90m": float(trend_close_90m),
        "cloud_top": cloud_top,
        "causal_floor": causal_floor,
        "emergency_stop": emergency_stop,
        "structural_stop": stop,
        "structural_stop_fraction": (entry - stop) / entry,
    }


class Candidate47IchiFanStructuralStrategy(_exact.Candidate47IchiFanStrategy):
    """Exact public signal/exit policy with coherent hard-stop geometry."""

    def __init__(self, config: Candidate47IchiFanStructuralConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "ichifan_structural_stop_candidates": 0,
                "ichifan_structural_stop_submissions": 0,
                "ichifan_structural_stop_failures": 0,
                "ichifan_structural_stop_distance_sum": 0.0,
                "ichifan_structural_stop_distance_min": None,
                "ichifan_structural_stop_distance_max": None,
                "ichifan_risk_geometry": "signal-low-and-90m-ema-and-cloud-with-10pct-emergency",
            }
        )

    def _submit_decision(self, decision: _router.RouteDecision, ts_event: int) -> None:
        if decision.side <= 0:
            super()._submit_decision(decision, ts_event)
            return

        self.diagnostics["ichifan_structural_stop_candidates"] += 1
        symbol = decision.symbol
        five_minute = _exact.aggregate_five_minute(tuple(self.bars[symbol]))
        states = _exact.fan_states(five_minute)
        if len(five_minute) < 2 or not states or not states[-1].ready:
            self.diagnostics["ichifan_structural_stop_failures"] += 1
            self._event(
                "ICHIFAN_STRUCTURAL_STOP_UNAVAILABLE",
                ts_event,
                symbol=symbol,
                reason="INCOMPLETE_CAUSAL_STATE",
            )
            return

        state = states[-1]
        signal_bar = five_minute[-2]  # every source indicator is shifted one bar
        entry = float(self.bars[symbol][-1].close)
        try:
            stop, geometry = causal_structural_stop(
                entry=entry,
                signal_bar_low=float(signal_bar.low),
                trend_close_90m=float(state.trend_close_90m),
                cloud_a=float(state.cloud_a),
                cloud_b=float(state.cloud_b),
            )
        except ValueError as error:
            self.diagnostics["ichifan_structural_stop_failures"] += 1
            self._event(
                "ICHIFAN_STRUCTURAL_STOP_UNAVAILABLE",
                ts_event,
                symbol=symbol,
                reason=str(error),
            )
            return

        distance = float(geometry["structural_stop_fraction"])
        self.diagnostics["ichifan_structural_stop_distance_sum"] += distance
        current_min = self.diagnostics["ichifan_structural_stop_distance_min"]
        current_max = self.diagnostics["ichifan_structural_stop_distance_max"]
        self.diagnostics["ichifan_structural_stop_distance_min"] = (
            distance if current_min is None else min(float(current_min), distance)
        )
        self.diagnostics["ichifan_structural_stop_distance_max"] = (
            distance if current_max is None else max(float(current_max), distance)
        )

        diagnostics = dict(decision.diagnostics)
        diagnostics.update(
            {
                "risk_geometry": "CAUSAL_STRUCTURAL_INVALIDATION",
                **geometry,
                "source_emergency_stop_reference": float(decision.stop_reference),
                "source_emergency_stop_fraction": 0.10,
            }
        )
        structural_decision = replace(
            decision,
            stop_reference=stop,
            diagnostics=diagnostics,
        )
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(structural_decision, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before:
            self.diagnostics["ichifan_structural_stop_submissions"] += 1
            if self.current_scenario is not None:
                self.current_scenario.update(
                    {
                        "candidate": "candidate-47-public-ichiv2-structural-risk",
                        "risk_geometry": "CAUSAL_STRUCTURAL_INVALIDATION",
                        "source_emergency_stop_reference": float(decision.stop_reference),
                        **geometry,
                    }
                )


Candidate35Strategy = Candidate47IchiFanStructuralStrategy
