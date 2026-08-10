"""N-to-1 synthesis: Candidate 47 IchiFan entry + Slope winner engine.

This module keeps the pinned Candidate 47 causal rising-edge entry, four-asset
one-slot arbitration and structural stop.  It changes only position management
in named experiments:

* ``source_control``: Candidate 47's 90-minute cross and 8%/6% trail;
* ``tight_trail_source_cross``: the public Slope-is-Dope trail (1.05%
  activation, 0.5% distance) runs before the original cross;
* ``tight_trail_underwater_thesis``: the same trail plus a loss-only failure of
  the source fan/90-minute thesis;
* ``tight_trail_no_signal``: the same trail without a source-signal exit.

The tight trail values are not tuned here.  They are the underlying-price
values of the public Slope policy's 2x-leverage 2.1% activation and 1.0%
distance.  Completed minute/five-minute observations remain causal, and
NautilusTrader continues to own matching, fees, slippage, brackets, positions
and account NAV.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

import ichifan_strategy as _source
import ichifan_structural_strategy as _struct


class Candidate51IchiFanN1Config(_struct.Candidate47IchiFanStructuralConfig, frozen=True):
    ichifan_n1_management_mode: str = "source_control"
    ichifan_n1_tight_trail_activation_fraction: float = 0.0105
    ichifan_n1_tight_trail_distance_fraction: float = 0.005


class Candidate51IchiFanN1Strategy(_struct.Candidate47IchiFanStructuralStrategy):
    """Role-separated management around the frozen Candidate 47 entry."""

    def __init__(self, config: Candidate51IchiFanN1Config) -> None:
        super().__init__(config)
        activation = float(config.ichifan_n1_tight_trail_activation_fraction)
        distance = float(config.ichifan_n1_tight_trail_distance_fraction)
        if abs(activation - 0.0105) > 1e-12 or abs(distance - 0.005) > 1e-12:
            raise ValueError("the reused Slope trailing engine must remain frozen at 1.05%/0.5%")
        self._n1_exit_pending = False
        self._n1_trail_active = False
        self._n1_trail_best = math.nan
        self.diagnostics.update(
            {
                "ichifan_n1_management_mode": str(config.ichifan_n1_management_mode),
                "ichifan_n1_tight_trail_activation_fraction": activation,
                "ichifan_n1_tight_trail_distance_fraction": distance,
                "ichifan_n1_path_updates": 0,
                "ichifan_n1_tight_trail_activations": 0,
                "ichifan_n1_tight_trail_exits": 0,
                "ichifan_n1_source_cross_exits": 0,
                "ichifan_n1_underwater_thesis_exits": 0,
                "ichifan_n1_forced_exits": 0,
                "ichifan_n1_exit_counts": {},
            }
        )

    def _reset_n1_state(self) -> None:
        self._n1_exit_pending = False
        self._n1_trail_active = False
        self._n1_trail_best = math.nan

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._reset_n1_state()

    def _after_position_opened(self, event: Any, scenario: dict[str, Any]) -> None:
        del event
        entry = float(
            scenario.get("actual_entry_fill")
            or scenario.get("entry_reference")
            or math.nan
        )
        self._n1_exit_pending = False
        self._n1_trail_active = False
        self._n1_trail_best = entry
        scenario.update(
            {
                "ichifan_n1_management_mode": str(self.config.ichifan_n1_management_mode),
                "ichifan_n1_mfe_fraction": 0.0,
                "ichifan_n1_mae_fraction": 0.0,
                "ichifan_n1_current_fraction": 0.0,
                "ichifan_n1_elapsed_minutes": 0,
                "ichifan_n1_first_positive_minute": None,
                "ichifan_n1_first_activation_minute": None,
                "ichifan_n1_exit_driver": None,
                "ichifan_n1_exit_details": None,
                "ichifan_n1_trail_best": entry if math.isfinite(entry) else None,
            }
        )

    def _after_position_closed(self, event: Any, record: dict[str, Any]) -> None:
        del event, record
        self._reset_n1_state()

    def _request_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._n1_exit_pending:
            return
        self._n1_exit_pending = True
        if self.current_scenario is not None:
            self.current_scenario["ichifan_n1_exit_driver"] = reason
            self.current_scenario["ichifan_n1_exit_details"] = details
        counts = self.diagnostics["ichifan_n1_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        if reason == "ICHIFAN_N1_TIGHT_TRAILING_EXIT":
            self.diagnostics["ichifan_n1_tight_trail_exits"] += 1
        elif reason == "ICHIFAN_90M_CROSS_EXIT":
            self.diagnostics["ichifan_n1_source_cross_exits"] += 1
        elif reason == "ICHIFAN_N1_UNDERWATER_THESIS_FAILURE":
            self.diagnostics["ichifan_n1_underwater_thesis_exits"] += 1
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("ICHIFAN_N1_EXIT", ts_event, reason=reason, **details)

    def _update_n1_path(self) -> tuple[float, float, int] | None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return None
        entry = float(
            scenario.get("actual_entry_fill")
            or scenario.get("entry_reference")
            or math.nan
        )
        side = int(scenario.get("side") or 0)
        if not math.isfinite(entry) or entry <= 0.0 or side not in (-1, 1):
            return None
        bar = self.bars[symbol][-1]
        favourable_price = float(bar.high) if side > 0 else float(bar.low)
        adverse_price = float(bar.low) if side > 0 else float(bar.high)
        favourable = side * (favourable_price - entry) / entry
        adverse = side * (adverse_price - entry) / entry
        current = side * (float(bar.close) - entry) / entry
        held = max(0, self.minute_index - self.position_open_minute)
        prior_mfe = float(scenario.get("ichifan_n1_mfe_fraction") or 0.0)
        prior_mae = float(scenario.get("ichifan_n1_mae_fraction") or 0.0)
        scenario.update(
            {
                "ichifan_n1_mfe_fraction": max(prior_mfe, favourable),
                "ichifan_n1_mae_fraction": min(prior_mae, adverse),
                "ichifan_n1_current_fraction": current,
                "ichifan_n1_elapsed_minutes": held,
            }
        )
        if scenario.get("ichifan_n1_first_positive_minute") is None and current > 0.0:
            scenario["ichifan_n1_first_positive_minute"] = held
        self.diagnostics["ichifan_n1_path_updates"] += 1
        return entry, current, held

    def _latest_fan_state(self) -> _source.FanState | None:
        symbol = self.current_symbol
        if symbol is None:
            return None
        five = _source.aggregate_five_minute(tuple(self.bars[symbol]))
        states = _source.fan_states(five)
        return states[-1] if states and states[-1].ready else None

    def _manage_tight_trail(
        self,
        ts_event: int,
        entry: float,
        held: int,
    ) -> bool:
        symbol = self.current_symbol
        scenario = self.current_scenario
        if symbol is None or scenario is None or not self.bars[symbol]:
            return False
        bar = self.bars[symbol][-1]
        distance = float(self.config.ichifan_n1_tight_trail_distance_fraction)
        activation = float(self.config.ichifan_n1_tight_trail_activation_fraction)

        # Preserve the public Slope engine's causal ordering: an already-active
        # trail is checked against the prior best before this completed bar may
        # activate or advance the best.  A bar cannot both activate and stop the
        # trail using an unknowable intrabar ordering.
        if self._n1_trail_active and math.isfinite(self._n1_trail_best):
            trail = self._n1_trail_best * (1.0 - distance)
            if float(bar.low) <= trail:
                self._request_exit(
                    ts_event,
                    "ICHIFAN_N1_TIGHT_TRAILING_EXIT",
                    trail_level=trail,
                    prior_best=self._n1_trail_best,
                    activation_fraction=activation,
                    distance_fraction=distance,
                    held_minutes=held,
                )
                return True

        favourable = (float(bar.high) - entry) / entry
        if not self._n1_trail_active and favourable >= activation:
            self._n1_trail_active = True
            self._n1_trail_best = float(bar.high)
            self.diagnostics["ichifan_n1_tight_trail_activations"] += 1
            scenario["ichifan_n1_first_activation_minute"] = held
        elif self._n1_trail_active:
            self._n1_trail_best = max(self._n1_trail_best, float(bar.high))
        scenario["ichifan_n1_trail_best"] = (
            self._n1_trail_best if math.isfinite(self._n1_trail_best) else None
        )
        scenario["ichifan_n1_trail_active"] = bool(self._n1_trail_active)
        return False

    def _run_generic_daytrade_management(self, ts_event: int) -> None:
        scenario = self.current_scenario
        before = len(self.events)
        _source._base.Candidate35Strategy._manage_open_position(self, ts_event)
        if scenario is None or scenario.get("ichifan_n1_exit_driver") is not None:
            return
        forced = any(
            item.get("event_type") == "FORCED_DAYTRADE_EXIT"
            for item in self.events[before:]
        )
        if forced:
            scenario["ichifan_n1_exit_driver"] = "FORCED_DAYTRADE_EXIT"
            scenario["ichifan_n1_exit_details"] = {}
            self._n1_exit_pending = True
            self.diagnostics["ichifan_n1_forced_exits"] += 1
            counts = self.diagnostics["ichifan_n1_exit_counts"]
            counts["FORCED_DAYTRADE_EXIT"] = int(counts.get("FORCED_DAYTRADE_EXIT", 0)) + 1

    def _manage_open_position(self, ts_event: int) -> None:
        if self._n1_exit_pending:
            return
        packed = self._update_n1_path()
        mode = str(self.config.ichifan_n1_management_mode).strip().lower()
        if mode == "source_control":
            before = len(self.events)
            super()._manage_open_position(ts_event)
            scenario = self.current_scenario
            if scenario is not None and scenario.get("ichifan_n1_exit_driver") is None:
                if any(
                    item.get("event_type") == "FORCED_DAYTRADE_EXIT"
                    for item in self.events[before:]
                ):
                    scenario["ichifan_n1_exit_driver"] = "FORCED_DAYTRADE_EXIT"
                    scenario["ichifan_n1_exit_details"] = {}
                    self._n1_exit_pending = True
                    self.diagnostics["ichifan_n1_forced_exits"] += 1
                    counts = self.diagnostics["ichifan_n1_exit_counts"]
                    counts["FORCED_DAYTRADE_EXIT"] = int(
                        counts.get("FORCED_DAYTRADE_EXIT", 0)
                    ) + 1
            return
        if mode not in {
            "tight_trail_source_cross",
            "tight_trail_underwater_thesis",
            "tight_trail_no_signal",
        }:
            raise ValueError(f"unsupported ichifan_n1_management_mode={mode!r}")
        if packed is None:
            self._run_generic_daytrade_management(ts_event)
            return
        entry, current, held = packed
        if self._manage_tight_trail(ts_event, entry, held):
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 == 4:
            state = self._latest_fan_state()
            if mode == "tight_trail_source_cross":
                if state is not None and state.exit_cross_down:
                    self._request_exit(
                        ts_event,
                        "ICHIFAN_90M_CROSS_EXIT",
                        trend_close_5m=float(state.trend_close_5m),
                        trend_close_90m=float(state.trend_close_90m),
                        fan_magnitude=float(state.fan_magnitude),
                        current_return_fraction=current,
                        held_minutes=held,
                    )
                    return
            elif mode == "tight_trail_underwater_thesis":
                if state is not None:
                    below_slow = float(state.trend_close_5m) < float(state.trend_close_90m)
                    fan_failed = float(state.fan_magnitude) <= 1.0
                    if current <= 0.0 and (below_slow or fan_failed):
                        self._request_exit(
                            ts_event,
                            "ICHIFAN_N1_UNDERWATER_THESIS_FAILURE",
                            below_90m=int(below_slow),
                            fan_failed=int(fan_failed),
                            fan_magnitude=float(state.fan_magnitude),
                            fan_gain=float(state.fan_gain),
                            trend_close_5m=float(state.trend_close_5m),
                            trend_close_90m=float(state.trend_close_90m),
                            current_return_fraction=current,
                            held_minutes=held,
                        )
                        return

        self._run_generic_daytrade_management(ts_event)


Candidate35Config = Candidate51IchiFanN1Config
Candidate35Strategy = Candidate51IchiFanN1Strategy

__all__ = [
    "Candidate35Config",
    "Candidate35Strategy",
    "Candidate51IchiFanN1Config",
    "Candidate51IchiFanN1Strategy",
]
