"""Frozen causal repair for profitable first Ichi source-exit crossovers.

The verified finite-history public IchiV2 policy is inherited unchanged.  A
source crossover remains an immediate exit when the position is non-positive
after the frozen round-trip cost allowance.  Only a profitable first crossover
is buffered until either after-cost break-even is lost or the exit state
persists on the next distinct completed five-minute candle.
"""
from __future__ import annotations

import math
from typing import Any

from router import ICHI_STATE, _aggregate_complete, _source_arrays
from strategy_ichi_v2_fast_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    ichi_profit_buffer_round_trip_cost_fraction: float = 0.0021


class Candidate35Strategy(_BaseStrategy):
    def __init__(self, config: Candidate35Config) -> None:
        cost = float(config.ichi_profit_buffer_round_trip_cost_fraction)
        if not math.isfinite(cost) or cost < 0.0:
            raise ValueError("profit-buffer round-trip cost must be non-negative")
        super().__init__(config)
        self._ichi_profit_buffer_pending: dict[str, Any] | None = None
        self.diagnostics.update(
            {
                "candidate57_ichi_source_exit_profit_buffer_v4": 1,
                "ichi_profit_buffer_round_trip_cost_fraction": cost,
                "ichi_profit_buffer_arms": 0,
                "ichi_profit_buffer_disarms": 0,
                "ichi_profit_buffer_immediate_nonpositive_exits": 0,
                "ichi_profit_buffer_break_even_exits": 0,
                "ichi_profit_buffer_confirmed_exits": 0,
                "ichi_profit_buffer_roi_resolutions": 0,
                "ichi_profit_buffer_policy_changed_entries": 0,
                "ichi_profit_buffer_thresholds_searched": 0,
            }
        )

    def _scenario_key(self) -> str:
        scenario = self.current_scenario or {}
        return str(
            scenario.get("scenario_id")
            or scenario.get("episode_ts")
            or f"{self.current_symbol}:{self.position_open_minute}"
        )

    def _completed_5m_context(self) -> tuple[int | None, list[Any]]:
        if self.current_symbol is None:
            return None, []
        candles = _aggregate_complete(
            tuple(self.bars[self.current_symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )
        if not candles:
            return None, []
        return int(candles[-1].ts_event), candles

    def _estimated_after_cost_fraction(self) -> float:
        scenario = self.current_scenario or {}
        side = int(scenario.get("side", 0))
        entry = float(scenario.get("entry_reference", 0.0))
        if (
            self.current_symbol is None
            or side not in (-1, 1)
            or not math.isfinite(entry)
            or entry <= 0.0
            or not self.bars[self.current_symbol]
        ):
            return math.nan
        close = float(self.bars[self.current_symbol][-1].close)
        gross = side * (close - entry) / entry
        return gross - float(self.config.ichi_profit_buffer_round_trip_cost_fraction)

    def _source_exit_state_active(self) -> tuple[bool, dict[str, float | int | str]]:
        scenario = self.current_scenario or {}
        side = int(scenario.get("side", 0))
        completed_ts, candles = self._completed_5m_context()
        required = max(
            100,
            int(self.config.ichi_lagging_span_period)
            + int(self.config.ichi_displacement)
            + 2,
        )
        if side not in (-1, 1) or len(candles) < required:
            return False, {
                "profit_buffer_state_ready": 0,
                "profit_buffer_completed_candle_ts": int(completed_ts or 0),
            }
        arrays = _source_arrays(candles, self.route_config)
        trend = arrays["trend_close_5m"]
        source = arrays[str(self.config.ichi_exit_indicator)]
        current_trend = float(trend[-1])
        current_source = float(source[-1])
        if not math.isfinite(current_trend) or not math.isfinite(current_source):
            return False, {
                "profit_buffer_state_ready": 0,
                "profit_buffer_completed_candle_ts": int(completed_ts or 0),
            }
        active = (
            current_trend < current_source
            if side > 0
            else current_trend > current_source
        )
        return bool(active), {
            "profit_buffer_state_ready": 1,
            "profit_buffer_state_active": int(active),
            "profit_buffer_completed_candle_ts": int(completed_ts or 0),
            "profit_buffer_current_trend": current_trend,
            "profit_buffer_current_indicator": current_source,
        }

    def _arm_profit_buffer(
        self,
        *,
        completed_candle_ts: int,
        estimated_after_cost_fraction: float,
        snapshot: dict[str, float | int | str],
    ) -> None:
        elapsed = max(0, self.minute_index - self.position_open_minute)
        self._ichi_profit_buffer_pending = {
            "scenario_key": self._scenario_key(),
            "armed_completed_candle_ts": int(completed_candle_ts),
            "armed_minute_index": int(self.minute_index),
            "armed_elapsed_minutes": int(elapsed),
            "armed_after_cost_fraction": float(estimated_after_cost_fraction),
        }
        scenario = self.current_scenario
        if scenario is not None:
            diagnostics = scenario.get("diagnostics")
            if not isinstance(diagnostics, dict):
                diagnostics = {}
                scenario["diagnostics"] = diagnostics
            diagnostics.update(
                {
                    "profit_buffer_armed": 1,
                    "profit_buffer_armed_completed_candle_ts": int(
                        completed_candle_ts
                    ),
                    "profit_buffer_armed_elapsed_minutes": int(elapsed),
                    "profit_buffer_armed_after_cost_fraction": float(
                        estimated_after_cost_fraction
                    ),
                    **{
                        f"profit_buffer_arm_{key}": value
                        for key, value in snapshot.items()
                    },
                }
            )
        self.diagnostics["ichi_profit_buffer_arms"] += 1
        self._event(
            "PUBLIC_ICHI_PROFIT_BUFFER_ARMED",
            int(completed_candle_ts),
            elapsed_minutes=int(elapsed),
            estimated_after_cost_fraction=float(estimated_after_cost_fraction),
            economic_boundary_fraction=0.0,
            confirmation_requires_next_distinct_completed_5m=True,
        )

    def _source_exit_signal(
        self,
    ) -> tuple[bool, dict[str, float | int | str]]:
        crossed, source_snapshot = super()._source_exit_signal()
        scenario = self.current_scenario or {}
        if scenario.get("state") != ICHI_STATE:
            self._ichi_profit_buffer_pending = None
            return crossed, source_snapshot

        scenario_key = self._scenario_key()
        pending = self._ichi_profit_buffer_pending
        if pending is not None and str(pending.get("scenario_key")) != scenario_key:
            self._ichi_profit_buffer_pending = None
            pending = None

        completed_ts, _ = self._completed_5m_context()
        estimated_net = self._estimated_after_cost_fraction()

        if pending is not None:
            if math.isfinite(estimated_net) and estimated_net <= 0.0:
                return True, {
                    **source_snapshot,
                    "source_exit_policy_resolution": "break_even_fallback",
                    "profit_buffer_estimated_after_cost_fraction": float(
                        estimated_net
                    ),
                    "profit_buffer_armed_completed_candle_ts": int(
                        pending["armed_completed_candle_ts"]
                    ),
                }

            armed_ts = int(pending["armed_completed_candle_ts"])
            if completed_ts is not None and int(completed_ts) > armed_ts:
                active, state_snapshot = self._source_exit_state_active()
                if active:
                    return True, {
                        **source_snapshot,
                        **state_snapshot,
                        "source_exit_policy_resolution": "persistent_confirmation",
                        "profit_buffer_estimated_after_cost_fraction": float(
                            estimated_net
                        ),
                        "profit_buffer_armed_completed_candle_ts": armed_ts,
                    }
                self.diagnostics["ichi_profit_buffer_disarms"] += 1
                self._event(
                    "PUBLIC_ICHI_PROFIT_BUFFER_DISARMED",
                    int(completed_ts),
                    armed_completed_candle_ts=armed_ts,
                    estimated_after_cost_fraction=(
                        float(estimated_net) if math.isfinite(estimated_net) else None
                    ),
                    reason="source_exit_state_recovered_on_next_completed_5m",
                    **state_snapshot,
                )
                self._ichi_profit_buffer_pending = None
            return False, source_snapshot

        if not crossed:
            return False, source_snapshot

        if completed_ts is None or not math.isfinite(estimated_net):
            return True, {
                **source_snapshot,
                "source_exit_policy_resolution": "immediate_unresolved_mark",
            }

        if estimated_net <= 0.0:
            self.diagnostics[
                "ichi_profit_buffer_immediate_nonpositive_exits"
            ] += 1
            return True, {
                **source_snapshot,
                "source_exit_policy_resolution": "immediate_nonpositive",
                "profit_buffer_estimated_after_cost_fraction": float(estimated_net),
                "profit_buffer_economic_boundary_fraction": 0.0,
            }

        self._arm_profit_buffer(
            completed_candle_ts=int(completed_ts),
            estimated_after_cost_fraction=float(estimated_net),
            snapshot=source_snapshot,
        )
        return False, source_snapshot

    def _close_ichi_position(
        self,
        event_name: str,
        ts_event: int,
        diagnostics_key: str,
        **payload: Any,
    ) -> None:
        resolution = str(payload.get("source_exit_policy_resolution") or "")
        pending_before = self._ichi_profit_buffer_pending is not None
        if event_name == "PUBLIC_ICHI_SOURCE_SIGNAL_EXIT":
            if resolution == "break_even_fallback":
                event_name = "PUBLIC_ICHI_PROFIT_BUFFER_BREAK_EVEN_EXIT"
                diagnostics_key = "ichi_profit_buffer_break_even_exits"
            elif resolution == "persistent_confirmation":
                event_name = "PUBLIC_ICHI_PROFIT_BUFFER_CONFIRMED_EXIT"
                diagnostics_key = "ichi_profit_buffer_confirmed_exits"
        elif event_name == "PUBLIC_ICHI_ROI_EXIT" and pending_before:
            self.diagnostics["ichi_profit_buffer_roi_resolutions"] += 1
            payload["profit_buffer_resolution"] = "UNCHANGED_PUBLIC_ROI"
        super()._close_ichi_position(
            event_name,
            ts_event,
            diagnostics_key,
            **payload,
        )
        self._ichi_profit_buffer_pending = None

    def on_position_closed(self, event: Any) -> None:
        self._ichi_profit_buffer_pending = None
        super().on_position_closed(event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
