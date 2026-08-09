"""Source-aligned hard-risk geometry for the public ichiV2 policy.

The public policy exits when shifted five-minute close crosses below shifted
90-minute EMA.  This variant places its protective stop at that completed-bar
EMA, bounded by the source 10% emergency stop.  No entry, fan, cloud, exit,
trailing, cost, account, or sizing threshold changes.
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ichifan_strategy as _exact
import router as _router

Candidate47IchiFanEmaBoundaryConfig = _exact.Candidate47IchiFanConfig
Candidate35Config = Candidate47IchiFanEmaBoundaryConfig
Candidate35StrategyBase = _exact._base.Candidate35Strategy


class Candidate47IchiFanEmaBoundaryStrategy(_exact.Candidate47IchiFanStrategy):
    def __init__(self, config: Candidate47IchiFanEmaBoundaryConfig) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "ema_boundary_candidates": 0,
                "ema_boundary_submissions": 0,
                "ema_boundary_failures": 0,
                "ema_boundary_distance_sum": 0.0,
                "ema_boundary_distance_min": None,
                "ema_boundary_distance_max": None,
                "risk_geometry": "shifted-90m-ema-with-source-10pct-emergency",
            }
        )

    def _submit_decision(self, decision: _router.RouteDecision, ts_event: int) -> None:
        if decision.side <= 0:
            Candidate35StrategyBase._submit_decision(self, decision, ts_event)
            return
        self.diagnostics["ema_boundary_candidates"] += 1
        symbol = decision.symbol
        states = _exact.fan_states(_exact.aggregate_five_minute(tuple(self.bars[symbol])))
        if not states or not states[-1].ready:
            self.diagnostics["ema_boundary_failures"] += 1
            return
        entry = float(self.bars[symbol][-1].close)
        ema = float(states[-1].trend_close_90m)
        emergency = entry * 0.90
        stop = max(emergency, ema)
        if not all(math.isfinite(value) for value in (entry, ema, stop)) or not 0.0 < stop < entry:
            self.diagnostics["ema_boundary_failures"] += 1
            self._event("ICHIFAN_EMA_BOUNDARY_UNAVAILABLE", ts_event, symbol=symbol, entry=entry, ema=ema)
            return
        distance = (entry - stop) / entry
        self.diagnostics["ema_boundary_distance_sum"] += distance
        current_min = self.diagnostics["ema_boundary_distance_min"]
        current_max = self.diagnostics["ema_boundary_distance_max"]
        self.diagnostics["ema_boundary_distance_min"] = distance if current_min is None else min(float(current_min), distance)
        self.diagnostics["ema_boundary_distance_max"] = distance if current_max is None else max(float(current_max), distance)
        mapped = replace(
            decision,
            stop_reference=stop,
            diagnostics={
                **dict(decision.diagnostics),
                "risk_geometry": "SHIFTED_90M_EMA_BOUNDARY",
                "shifted_90m_ema": ema,
                "source_emergency_stop": emergency,
                "structural_stop": stop,
                "structural_stop_fraction": distance,
            },
        )
        before = int(self.diagnostics["entry_submissions"])
        Candidate35StrategyBase._submit_decision(self, mapped, ts_event)
        if int(self.diagnostics["entry_submissions"]) > before:
            self.diagnostics["ema_boundary_submissions"] += 1
            if self.current_scenario is not None:
                self.current_scenario.update(
                    {
                        "candidate": "candidate-47-public-ichiv2-ema-boundary-risk",
                        "risk_geometry": "SHIFTED_90M_EMA_BOUNDARY",
                        "shifted_90m_ema": ema,
                        "source_emergency_stop": emergency,
                        "structural_stop_fraction": distance,
                    }
                )


Candidate35Strategy = Candidate47IchiFanEmaBoundaryStrategy
