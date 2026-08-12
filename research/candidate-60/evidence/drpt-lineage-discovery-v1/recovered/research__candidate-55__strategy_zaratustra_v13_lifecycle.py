"""Causal 15-minute state lifecycle for the high-capacity V13 short family.

The previous state-exit experiment treated a pre-existing invalid 15-minute
state as a post-entry transition.  It therefore closed and re-entered the same
causal episode repeatedly; many exits occurred one minute after entry using a
15-minute state timestamp that preceded the entry itself.

This repair gives the higher-timeframe state an explicit lifecycle:

* ``confirm_*`` admits a DI-only source entry only when the latest completed
  15-minute DI state is fully valid, then reacts only to a later completed
  15-minute transition.
* ``arm_*`` leaves source entries unchanged, but invalidation is disabled until
  a completed 15-minute state has first become fully valid during the trade.

Bollinger-origin and mixed DI+Bollinger entries retain source management.  The
source stop, trailing activation, trailing distance and risk sizing are
unchanged.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys


_BASE_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v13.py")
_BASE_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v13_lifecycle_base", _BASE_PATH
)
if _BASE_SPEC is None or _BASE_SPEC.loader is None:
    raise RuntimeError(f"cannot load V13 source execution: {_BASE_PATH}")
_BASE = importlib.util.module_from_spec(_BASE_SPEC)
sys.modules[_BASE_SPEC.name] = _BASE
_BASE_SPEC.loader.exec_module(_BASE)

_HELPER_PATH = Path(__file__).resolve().with_name("strategy_zaratustra_v13_state.py")
_HELPER_SPEC = importlib.util.spec_from_file_location(
    "candidate55_zaratustra_v13_lifecycle_helpers", _HELPER_PATH
)
if _HELPER_SPEC is None or _HELPER_SPEC.loader is None:
    raise RuntimeError(f"cannot load V13 state helpers: {_HELPER_PATH}")
_HELPER = importlib.util.module_from_spec(_HELPER_SPEC)
sys.modules[_HELPER_SPEC.name] = _HELPER
_HELPER_SPEC.loader.exec_module(_HELPER)


class Candidate35Config(_BASE.Candidate35Config, frozen=True):
    v13_lifecycle_mode: str = "source"


class Candidate35Strategy(_BASE.Candidate35Strategy):
    """V13 source entries with explicit higher-timeframe state ownership."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.v13_lifecycle_mode).strip().lower()
        if mode not in {
            "source",
            "confirm_strict",
            "confirm_majority",
            "arm_strict",
            "arm_majority",
        }:
            raise ValueError(f"unsupported V13 lifecycle mode: {mode}")
        self._lifecycle_mode = mode
        self._lifecycle_armed = False
        self._lifecycle_entry_state_ts = 0
        self._lifecycle_last_state_ts = 0
        self.diagnostics.update(
            {
                "candidate": "candidate-55",
                "candidate55_research_question": (
                    "REMOVE_PREEXISTING_STATE_CHURN_WITHOUT_DESTROYING_V13_"
                    "TRAILING_WINNER_ENGINE"
                ),
                "v13_lifecycle_mode": mode,
                "v13_lifecycle_entry_checks": 0,
                "v13_lifecycle_entry_accepts": 0,
                "v13_lifecycle_entry_rejections": 0,
                "v13_lifecycle_state_checks": 0,
                "v13_lifecycle_arms": 0,
                "v13_lifecycle_exits": 0,
                "v13_lifecycle_preentry_state_exit_possible": 0,
                "v13_lifecycle_duplicate_state_checks_skipped": 0,
                "entry_policy_source_thresholds_changed": 0,
                "source_stop_changed": 0,
                "source_trailing_changed": 0,
                "bollinger_origin_management_changed": 0,
                "complete_15m_state_only": 1,
            }
        )

    @property
    def _confirm_entry(self) -> bool:
        return self._lifecycle_mode.startswith("confirm_")

    @property
    def _lifecycle_enabled(self) -> bool:
        return self._lifecycle_mode != "source"

    @property
    def _failed_required(self) -> int:
        return 1 if self._lifecycle_mode.endswith("strict") else 2

    def _state(self, symbol: str, side: int):
        return _HELPER._di_components(
            tuple(self.bars[symbol]),
            timeframe=15,
            period=int(self.config.zaratustra_di_period),
            side=int(side),
        )

    @staticmethod
    def _di_only(decision_or_scenario) -> bool:
        diagnostics = (
            decision_or_scenario.diagnostics
            if hasattr(decision_or_scenario, "diagnostics")
            else decision_or_scenario.get("diagnostics", {})
        )
        return (
            int(diagnostics.get("used_di_component", 0)) == 1
            and int(diagnostics.get("used_bb_component", 0)) == 0
        )

    def _submit_decision(self, decision, ts_event: int) -> None:
        state_ts = 0
        initially_valid = False
        if self._lifecycle_enabled and self._di_only(decision):
            components, values = self._state(decision.symbol, int(decision.side))
            self.diagnostics["v13_lifecycle_entry_checks"] += 1
            state_ts = int(values.get("state_ts", 0))
            initially_valid = components is not None and all(components)
            if self._confirm_entry and not initially_valid:
                self.diagnostics["v13_lifecycle_entry_rejections"] += 1
                self._event(
                    "V13_LIFECYCLE_ENTRY_REJECTED",
                    ts_event,
                    symbol=decision.symbol,
                    side=int(decision.side),
                    state_ts=state_ts,
                    components=(
                        None if components is None else list(components)
                    ),
                    dx=values.get("dx"),
                    adx=values.get("adx"),
                    pdi=values.get("pdi"),
                    mdi=values.get("mdi"),
                    reason="COMPLETED_15M_STATE_NOT_VALID_AT_ENTRY",
                )
                return

        before = int(self.diagnostics.get("entry_submissions", 0))
        super()._submit_decision(decision, ts_event)
        after = int(self.diagnostics.get("entry_submissions", 0))
        if after <= before or self.current_scenario is None:
            return
        self.diagnostics["v13_lifecycle_entry_accepts"] += 1
        self._lifecycle_entry_state_ts = int(state_ts)
        self._lifecycle_last_state_ts = int(state_ts)
        self._lifecycle_armed = bool(initially_valid)
        if self._lifecycle_armed:
            self.diagnostics["v13_lifecycle_arms"] += 1
        self.current_scenario.update(
            {
                "v13_lifecycle_mode": self._lifecycle_mode,
                "v13_lifecycle_entry_state_ts": int(state_ts),
                "v13_lifecycle_initially_armed": bool(initially_valid),
            }
        )

    def _manage_open_position(self, ts_event: int) -> None:
        if (
            self._lifecycle_enabled
            and self.current_symbol is not None
            and self.current_scenario is not None
            and self._di_only(self.current_scenario)
        ):
            scenario = self.current_scenario
            side = int(scenario.get("side", 0))
            components, values = self._state(self.current_symbol, side)
            if components is not None:
                state_ts = int(values.get("state_ts", 0))
                if state_ts <= int(self._lifecycle_last_state_ts):
                    self.diagnostics[
                        "v13_lifecycle_duplicate_state_checks_skipped"
                    ] += 1
                else:
                    self._lifecycle_last_state_ts = state_ts
                    self.diagnostics["v13_lifecycle_state_checks"] += 1
                    fully_valid = all(components)
                    if not self._lifecycle_armed:
                        if fully_valid:
                            self._lifecycle_armed = True
                            self.diagnostics["v13_lifecycle_arms"] += 1
                            self._event(
                                "V13_LIFECYCLE_ARMED",
                                ts_event,
                                state_ts=state_ts,
                                components=list(components),
                            )
                    else:
                        failed = sum(not item for item in components)
                        if (
                            state_ts > int(self._lifecycle_entry_state_ts)
                            and failed >= self._failed_required
                        ):
                            instrument_id = self.instrument_ids[
                                self.current_symbol
                            ]
                            self.cancel_all_orders(instrument_id)
                            self.close_all_positions(instrument_id)
                            self.diagnostics["v13_lifecycle_exits"] += 1
                            self._event(
                                "V13_LIFECYCLE_STATE_TRANSITION_EXIT",
                                ts_event,
                                state_ts=state_ts,
                                entry_state_ts=int(
                                    self._lifecycle_entry_state_ts
                                ),
                                failed_components=failed,
                                failed_required=self._failed_required,
                                components=list(components),
                                dx=values.get("dx"),
                                adx=values.get("adx"),
                                pdi=values.get("pdi"),
                                mdi=values.get("mdi"),
                            )
                            return
        super()._manage_open_position(ts_event)

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._lifecycle_armed = False
        self._lifecycle_entry_state_ts = 0
        self._lifecycle_last_state_ts = 0


__all__ = ["Candidate35Config", "Candidate35Strategy"]
