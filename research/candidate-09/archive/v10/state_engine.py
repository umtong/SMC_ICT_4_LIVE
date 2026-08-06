"""Candidate 09 v10: exact controlled decomposition of the positive v4 run.

The archived v4 detector, acceptance/failure sequence, reversal invalidation and
equilibrium target are loaded without rewriting them. Baseline disables only the
two components whose v4 trade-level accounting was repeatedly negative:
continuation entries and 240-minute source levels. Ablations restore either one
or remove order-flow confirmation while all weeks, costs and risk contracts stay
unchanged.
"""

from __future__ import annotations

import importlib.util
import sys
from copy import deepcopy
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Mapping

HERE = Path(__file__).resolve().parent
ARCHIVED_V4 = HERE / "archive" / "v4" / "state_engine.py"
MODULE_NAME = "candidate09_archived_v4_state_engine"

spec = importlib.util.spec_from_file_location(MODULE_NAME, ARCHIVED_V4)
if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load archived v4 state engine: {ARCHIVED_V4}")
_v4 = importlib.util.module_from_spec(spec)
sys.modules[MODULE_NAME] = _v4
spec.loader.exec_module(_v4)

MINUTE_NS = _v4.MINUTE_NS
FlowBar = _v4.FlowBar
AuctionLevel = _v4.AuctionLevel
PendingResolution = _v4.PendingResolution
DiagnosticEvent = _v4.DiagnosticEvent
Signal = _v4.Signal
EngineResult = _v4.EngineResult
RiskSizing = _v4.RiskSizing
risk_based_quantity = _v4.risk_based_quantity


@dataclass(frozen=True, slots=True)
class EngineConfig(_v4.EngineConfig):
    enable_continuation_entries: bool = False

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any], *, ablation: str = "baseline") -> "EngineConfig":
        allowed = {"baseline", "with-continuation", "with-240m", "no-flow"}
        if ablation not in allowed:
            raise ValueError(f"unknown ablation: {ablation}")
        mapped = deepcopy(dict(payload))
        mapped["structure"] = dict(payload["structure"])
        horizons = [15, 60, 1440]
        if ablation == "with-240m":
            horizons = [15, 60, 240, 1440]
        mapped["structure"]["auction_horizons_minutes"] = horizons
        base_ablation = "no-flow" if ablation == "no-flow" else "baseline"
        base = _v4.EngineConfig.from_mapping(mapped, ablation=base_ablation)
        inherited = {field.name: getattr(base, field.name) for field in fields(_v4.EngineConfig)}
        return cls(**inherited, enable_continuation_entries=ablation == "with-continuation")


class LiquidityStateEngine(_v4.LiquidityStateEngine):
    config: EngineConfig

    def __init__(self, config: EngineConfig):
        super().__init__(config)
        self._continuation_suppressed = False

    def _build_signal(self, pending: PendingResolution, bar: FlowBar, *, branch: str) -> Signal | None:
        if branch == "CONTINUATION" and not self.config.enable_continuation_entries:
            self._continuation_suppressed = True
            return None
        return super()._build_signal(pending, bar, branch=branch)

    def _finish(
        self,
        pending: PendingResolution,
        bar: FlowBar,
        signal: Signal | None,
        events: list[DiagnosticEvent],
    ) -> Signal | None:
        if signal is None and self._continuation_suppressed:
            events.append(self._event(
                pending,
                bar,
                "SCENARIO_RESOLVED_WITHOUT_ENTRY",
                pending.state,
                "NO_TRADE",
                "CONTINUATION_BRANCH_DISABLED_BY_CONTROLLED_BASELINE",
                {"resolved_branch": "CONTINUATION"},
            ))
            self._pending = None
            self._cooldown = self.config.cooldown_bars
            self._continuation_suppressed = False
            return None
        self._continuation_suppressed = False
        return super()._finish(pending, bar, signal, events)


__all__ = [
    "MINUTE_NS",
    "AuctionLevel",
    "DiagnosticEvent",
    "EngineConfig",
    "EngineResult",
    "FlowBar",
    "LiquidityStateEngine",
    "PendingResolution",
    "RiskSizing",
    "Signal",
    "risk_based_quantity",
]
