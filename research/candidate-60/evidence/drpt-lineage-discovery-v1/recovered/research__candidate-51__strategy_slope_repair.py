"""Source-relative loss-tail repair for the public Slope-is-Dope system.

The public entry, structural invalidation, trailing and ROI winner engines are
left intact.  Repairs are evaluated only after those mechanisms and use either
loss-only source-thesis failure or progress checkpoints already present in the
public ROI schedule.  This prevents a good trailing winner from being replaced
by an arbitrary take-profit rule.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
from typing import Any

import router as _router
import strategy_slope_dope as _base


class Candidate35Config(_base.Candidate35Config, frozen=True):
    slope_min_separation_activation_multiple: float = 0.0
    # source | condition_loss | progress_thesis
    slope_repair_management: str = "source"
    slope_progress_checkpoint_1_minutes: int = 132
    slope_progress_checkpoint_2_minutes: int = 548
    slope_progress_checkpoint_3_minutes: int = 961
    slope_progress_activation_fraction_1: float = 0.25
    slope_progress_activation_fraction_2: float = 0.50
    slope_progress_activation_fraction_3: float = 1.00


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.route_config = replace(
            self.route_config,
            slope_trailing_offset_profit_ratio=float(
                config.slope_trailing_offset_profit_ratio
            ),
            slope_min_separation_activation_multiple=float(
                config.slope_min_separation_activation_multiple
            ),
        )
        self.diagnostics.update(
            {
                "slope_min_separation_activation_multiple": float(
                    config.slope_min_separation_activation_multiple
                ),
                "slope_repair_management": str(config.slope_repair_management),
                "slope_condition_loss_exits": 0,
                "slope_no_progress_exits": 0,
                "slope_repair_exit_counts": {},
            }
        )

    def _submit_repair_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._exit_pending:
            return
        if self.current_scenario is not None:
            self.current_scenario["slope_exit_driver"] = reason
            self.current_scenario["slope_repair_exit_details"] = details
        counts = self.diagnostics["slope_repair_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self._exit_pending = True
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("SLOPE_REPAIR_EXIT", ts_event, reason=reason, **details)

    def _repair_state(self) -> tuple[dict[str, float | int | str], float, float, int] | None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return None
        state = _router.inspect_state(tuple(self.bars[symbol]), self.route_config)
        if not int(state.get("ready") or 0):
            return None
        entry = float(
            scenario.get("actual_entry_fill")
            or scenario.get("entry_reference")
            or math.nan
        )
        side = int(scenario.get("side") or 0)
        if not math.isfinite(entry) or entry <= 0.0 or side not in (-1, 1):
            return None
        close = float(self.bars[symbol][-1].close)
        current = side * (close - entry) / entry
        mfe = float(scenario.get("slope_mfe_fraction") or 0.0)
        held = max(0, self.minute_index - self.position_open_minute)
        return state, current, mfe, held

    def _manage_open_position(self, ts_event: int) -> None:
        if self._exit_pending:
            return
        # Preserve public trailing/ROI, the selected source exit and the hard
        # structural stop.  Repairs act only if those mechanisms leave the
        # position open.
        super()._manage_open_position(ts_event)
        if self._exit_pending:
            return
        management = str(self.config.slope_repair_management).strip().lower()
        if management == "source":
            return
        if management not in {"condition_loss", "progress_thesis"}:
            raise ValueError(f"unsupported slope_repair_management={management!r}")
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute != 59:
            return
        packed = self._repair_state()
        if packed is None:
            return
        state, current, mfe, held = packed
        scenario = self.current_scenario or {}
        side = int(scenario.get("side") or 0)
        condition_active = bool(
            state.get("long_condition") if side > 0 else state.get("short_condition")
        )
        if not condition_active and current <= 0.0:
            self.diagnostics["slope_condition_loss_exits"] += 1
            self._submit_repair_exit(
                ts_event,
                "SOURCE_CONDITION_LOST_UNDERWATER",
                held_minutes=held,
                mfe_fraction=mfe,
                current_return_fraction=current,
                adx=float(state.get("adx") or 0.0),
                rsi=float(state.get("rsi") or 0.0),
                fast_sma=float(state.get("fast_sma") or 0.0),
                slow_sma=float(state.get("slow_sma") or 0.0),
            )
            return
        if management != "progress_thesis":
            return
        leverage = max(float(self.config.slope_source_leverage), 1e-12)
        activation = float(self.config.slope_trailing_offset_profit_ratio) / leverage
        checkpoints = (
            (
                int(self.config.slope_progress_checkpoint_3_minutes),
                float(self.config.slope_progress_activation_fraction_3),
                "NO_SOURCE_PROGRESS_BY_ROI_ZERO",
            ),
            (
                int(self.config.slope_progress_checkpoint_2_minutes),
                float(self.config.slope_progress_activation_fraction_2),
                "NO_SOURCE_PROGRESS_BY_ROI_STEP_2",
            ),
            (
                int(self.config.slope_progress_checkpoint_1_minutes),
                float(self.config.slope_progress_activation_fraction_1),
                "NO_SOURCE_PROGRESS_BY_ROI_STEP_1",
            ),
        )
        for minutes, fraction, reason in checkpoints:
            required = activation * fraction
            if held >= minutes and mfe < required and current <= 0.0:
                self.diagnostics["slope_no_progress_exits"] += 1
                self._submit_repair_exit(
                    ts_event,
                    reason,
                    held_minutes=held,
                    mfe_fraction=mfe,
                    required_mfe_fraction=required,
                    current_return_fraction=current,
                    condition_active=condition_active,
                    adx=float(state.get("adx") or 0.0),
                    rsi=float(state.get("rsi") or 0.0),
                )
                return


__all__ = ["Candidate35Config", "Candidate35Strategy"]
