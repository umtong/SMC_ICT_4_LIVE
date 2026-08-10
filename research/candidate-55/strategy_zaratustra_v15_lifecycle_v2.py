"""Lifecycle v2: bind the V15 acceptance episode from the first open-position minute.

Nautilus produced valid position-open events in v1, but the adapter callback path
did not execute the subclass binding hook.  All v1 variants therefore matched
the source exactly and were an implementation-null experiment.  This wrapper
moves only lifecycle ownership into the already-proven minute management path;
entry, one-slot arbitration, stop, trailing, sizing and acceptance predicates
remain unchanged.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys

_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v15_lifecycle.py")
_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v15_lifecycle_v1", _BASE_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load lifecycle v1: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    pass


class Candidate35Strategy(_BASE.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "v15_lifecycle_binding_version": 2,
                "v1_null_experiment_acknowledged": 1,
                "lifecycle_lazy_bind_attempts": 0,
                "lifecycle_lazy_bind_failures": 0,
            }
        )

    def _bind_lifecycle_from_open_scenario(self, ts_event: int) -> None:
        if not self._lifecycle_enabled or self._acceptance_bound:
            return
        if self.current_symbol is None or self.position_open_minute < 0:
            return
        self.diagnostics["lifecycle_lazy_bind_attempts"] += 1
        scenario = self.current_scenario or {}
        diagnostics = scenario.get("diagnostics", {})
        signal_ts = int(scenario.get("episode_ts") or 0)
        level = float(diagnostics.get("lower", math.nan))
        side = int(scenario.get("side", 0))
        is_bb = int(diagnostics.get("used_bb_component", 0)) == 1
        if side != -1 or not is_bb or signal_ts <= 0 or not math.isfinite(level):
            self.diagnostics["lifecycle_lazy_bind_failures"] += 1
            return
        self._acceptance_bound = True
        self._acceptance_satisfied = False
        self._acceptance_signal_ts = signal_ts
        self._acceptance_level = level
        self._acceptance_last_age = -1
        self.diagnostics["acceptance_positions_bound"] += 1
        self._event(
            "V15_ACCEPTANCE_LIFECYCLE_BOUND_V2",
            ts_event,
            symbol=self.current_symbol,
            signal_ts=signal_ts,
            accepted_level=level,
            deadline_minutes=int(self.config.v15_acceptance_deadline_minutes),
            lifecycle_mode=self._lifecycle_mode,
        )

    def _manage_open_position(self, ts_event: int) -> None:
        self._bind_lifecycle_from_open_scenario(ts_event)
        super()._manage_open_position(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
