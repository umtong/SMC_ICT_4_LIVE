"""Observation-only follow-through recorder for the frozen structural ichiFan.

Trading behavior is inherited unchanged from
``Candidate47IchiFanStructuralStrategy``.  This subclass only records causal
post-fill state at predeclared ages of 5, 10, 15, 30 and 60 completed universe
minutes.  It is designed to answer the next strategy question without using
future MFE as a rule: what observable state transition separates an expanding
auction from a failed fan-acceleration entry?
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import ichifan_strategy as _exact
import ichifan_structural_strategy as _structural

Candidate47IchiFanStructuralRecorderConfig = (
    _structural.Candidate47IchiFanStructuralConfig
)
Candidate35Config = Candidate47IchiFanStructuralRecorderConfig
SYMBOLS = _structural.SYMBOLS
_SNAPSHOT_MINUTES = (5, 10, 15, 30, 60)


class Candidate47IchiFanStructuralRecorderStrategy(
    _structural.Candidate47IchiFanStructuralStrategy,
):
    """Frozen structural policy plus non-intervening causal state capture."""

    def __init__(self, config: Candidate47IchiFanStructuralRecorderConfig) -> None:
        super().__init__(config)
        self.followthrough_snapshots: list[dict[str, Any]] = []
        self.diagnostics.update(
            {
                "followthrough_snapshot_schedule_minutes": list(_SNAPSHOT_MINUTES),
                "followthrough_snapshots_recorded": 0,
                "followthrough_snapshot_failures": 0,
                "followthrough_recorder_changes_trading_policy": False,
            }
        )

    def on_position_opened(self, event: Any) -> None:
        super().on_position_opened(event)
        scenario = self.current_scenario
        if scenario is None:
            return
        symbol = self.current_symbol
        if symbol is None or not self.bars[symbol]:
            return
        latest = self.bars[symbol][-1]
        five_minute = _exact.aggregate_five_minute(tuple(self.bars[symbol]))
        signal_high = (
            float(five_minute[-2].high)
            if len(five_minute) >= 2
            else math.nan
        )
        scenario["followthrough_fill_ts"] = int(
            getattr(event, "ts_event", latest.ts_event)
        )
        scenario["followthrough_fill_minute_index"] = int(self.minute_index)
        scenario["followthrough_recorded_offsets"] = []
        scenario["followthrough_peak"] = float(latest.high)
        scenario["followthrough_trough"] = float(latest.low)
        scenario["followthrough_signal_high"] = signal_high

    def _record_followthrough(self, ts_event: int) -> None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return
        fill_index = int(
            scenario.get("followthrough_fill_minute_index", self.position_open_minute)
        )
        if fill_index < 0:
            return
        age = int(self.minute_index - fill_index)
        recorded = {
            int(value) for value in scenario.get("followthrough_recorded_offsets", [])
        }
        due = [minute for minute in _SNAPSHOT_MINUTES if minute <= age and minute not in recorded]
        if not due:
            return

        latest = self.bars[symbol][-1]
        peak = max(
            float(scenario.get("followthrough_peak", latest.high)),
            float(latest.high),
        )
        trough = min(
            float(scenario.get("followthrough_trough", latest.low)),
            float(latest.low),
        )
        scenario["followthrough_peak"] = peak
        scenario["followthrough_trough"] = trough

        entry = float(scenario.get("entry_reference", math.nan))
        stop = float(scenario.get("stop", math.nan))
        initial_risk = entry - stop
        if not (
            math.isfinite(entry)
            and math.isfinite(stop)
            and math.isfinite(initial_risk)
            and initial_risk > 0.0
        ):
            self.diagnostics["followthrough_snapshot_failures"] += len(due)
            return

        five_minute = _exact.aggregate_five_minute(tuple(self.bars[symbol]))
        states = _exact.fan_states(five_minute)
        state = states[-1] if states and states[-1].ready else None
        signal_high = float(scenario.get("followthrough_signal_high", math.nan))

        close = float(latest.close)
        open_price = float(latest.open)
        one_minute_return_bps = (
            math.log(close / open_price) * 10_000.0
            if close > 0.0 and open_price > 0.0
            else math.nan
        )
        for minute in due:
            snapshot = {
                "scenario_id": scenario.get("scenario_id"),
                "causal_episode_id": scenario.get("causal_episode_id"),
                "symbol": symbol,
                "snapshot_ts": int(ts_event),
                "age_minutes": int(minute),
                "actual_age_minutes": age,
                "entry_reference": entry,
                "initial_stop": stop,
                "initial_risk_per_unit": initial_risk,
                "signal_high": signal_high if math.isfinite(signal_high) else None,
                "close": close,
                "high_to_date": peak,
                "low_to_date": trough,
                "close_r": (close - entry) / initial_risk,
                "mfe_r_to_date": (peak - entry) / initial_risk,
                "mae_r_to_date": (trough - entry) / initial_risk,
                "above_entry": close > entry,
                "above_signal_high": (
                    close > signal_high if math.isfinite(signal_high) else None
                ),
                "one_minute_return_bps": one_minute_return_bps,
                "fan_state_ready": state is not None,
                "fan_entry_state_still_true": (
                    bool(state.entry) if state is not None else None
                ),
                "fan_magnitude": (
                    float(state.fan_magnitude) if state is not None else None
                ),
                "fan_gain": float(state.fan_gain) if state is not None else None,
                "shifted_close_5m": (
                    float(state.trend_close_5m) if state is not None else None
                ),
                "shifted_close_90m": (
                    float(state.trend_close_90m) if state is not None else None
                ),
                "close_above_shifted_90m": (
                    close > float(state.trend_close_90m)
                    if state is not None
                    else None
                ),
                "cloud_top": (
                    max(float(state.cloud_a), float(state.cloud_b))
                    if state is not None
                    else None
                ),
            }
            self.followthrough_snapshots.append(snapshot)
            recorded.add(minute)
            self.diagnostics["followthrough_snapshots_recorded"] += 1
        scenario["followthrough_recorded_offsets"] = sorted(recorded)

    def _manage_open_position(self, ts_event: int) -> None:
        symbol = self.current_symbol
        if symbol is not None and self.bars[symbol]:
            latest = self.bars[symbol][-1]
            scenario = self.current_scenario
            if scenario is not None:
                scenario["followthrough_peak"] = max(
                    float(scenario.get("followthrough_peak", latest.high)),
                    float(latest.high),
                )
                scenario["followthrough_trough"] = min(
                    float(scenario.get("followthrough_trough", latest.low)),
                    float(latest.low),
                )
            self._record_followthrough(ts_event)
        super()._manage_open_position(ts_event)

    def on_stop(self) -> None:
        super().on_stop()
        destination = Path(self.config.output_dir)
        (destination / "followthrough_snapshots.json").write_text(
            json.dumps(
                self.followthrough_snapshots,
                indent=2,
                sort_keys=True,
                allow_nan=False,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )


Candidate35Strategy = Candidate47IchiFanStructuralRecorderStrategy
