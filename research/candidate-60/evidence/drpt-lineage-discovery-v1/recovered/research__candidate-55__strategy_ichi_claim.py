"""Claim-period management ablations for the public ichiV1 family.

Entry logic, one-episode semantics, ROI schedule, source stop/EMA invalidation,
risk sizing, costs and one global slot are inherited unchanged.  The only
predeclared ablation disables the public EMA-cross exit so its strongly negative
reported exit bucket can be tested as a protection mechanism rather than assumed
to be a defect.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from router import ICHI_STATE
from strategy_base import Candidate35Strategy as _ExecutionShell

_BASE_PATH = Path(__file__).resolve().with_name("strategy_ichi.py")
_SPEC = importlib.util.spec_from_file_location("candidate55_ichi_claim_base", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load inherited Ichi strategy: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    ichi_exit_signal_mode: str = "source"  # source | disabled


class Candidate35Strategy(_BASE.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.ichi_exit_signal_mode).strip().lower()
        if mode not in {"source", "disabled"}:
            raise ValueError(f"unsupported Ichi exit-signal mode: {mode}")
        self._ichi_exit_signal_mode = mode
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "candidate55_research_question": (
                    "DOES_THE_PUBLIC_ICHI_GROSS_PROFIT_ENGINE_SURVIVE_ONE_SLOT_"
                    "RISK_SIZING_AND_IS_THE_EMA_EXIT_A_REPAIRABLE_LOSS_ENGINE"
                ),
                "ichi_exit_signal_mode": mode,
                "source_entry_changed": 0,
                "source_roi_changed": 0,
                "source_risk_geometry_changed_by_this_ablation": 0,
                "one_global_slot": 1,
            }
        )

    def _manage_open_position(self, ts_event: int) -> None:
        if self._ichi_exit_signal_mode == "source":
            super()._manage_open_position(ts_event)
            return
        if self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        if scenario.get("state") == ICHI_STATE:
            side = int(scenario.get("side", 0))
            entry = float(scenario.get("entry_reference", 0.0))
            bar = self.bars[self.current_symbol][-1]
            if side in (-1, 1) and entry > 0.0:
                elapsed = max(0, self.minute_index - self.position_open_minute)
                roi_fraction = self._roi_fraction(elapsed)
                target = entry * (1.0 + side * roi_fraction)
                hit = float(bar.high) >= target if side > 0 else float(bar.low) <= target
                if hit:
                    instrument_id = self.instrument_ids[self.current_symbol]
                    self.cancel_all_orders(instrument_id)
                    self.close_all_positions(instrument_id)
                    self.diagnostics["ichi_roi_exits"] += 1
                    self._event(
                        "PUBLIC_ICHI_ROI_EXIT",
                        ts_event,
                        elapsed_minutes=elapsed,
                        roi_fraction=roi_fraction,
                        target=target,
                        source_exit_disabled=1,
                    )
                    return
        _ExecutionShell._manage_open_position(self, ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
