"""Behaviour-preserving lifecycle audit for the public ichiV2 short family.

The verified finite-history source policy is inherited unchanged.  This wrapper
records actual-trade paths and evolves non-trading shadows after source exits.
No shadow submits, cancels, modifies, or closes an account order.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from router import ICHI_STATE
from strategy_ichi_v2_fast_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    ichi_forensic_round_trip_cost_fraction: float = 0.0021


class Candidate35Strategy(_BaseStrategy):
    _MFE_THRESHOLDS_R = (0.10, 0.25)
    _MAE_THRESHOLDS_R = (0.10, 0.25, 0.50)

    def __init__(self, config: Candidate35Config) -> None:
        if float(config.ichi_forensic_round_trip_cost_fraction) < 0.0:
            raise ValueError("forensic cost fraction must be non-negative")
        super().__init__(config)
        self._ichi_post_exit_active: list[dict[str, Any]] = []
        self._ichi_post_exit_completed: list[dict[str, Any]] = []
        self.diagnostics.update(
            {
                "candidate57_ichi_short_lifecycle_forensic_v3": 1,
                "ichi_short_forensic_policy_changed": 0,
                "ichi_short_forensic_source_checks": 0,
                "ichi_short_forensic_active_source_checks": 0,
                "ichi_short_post_exit_shadows_started": 0,
                "ichi_short_post_exit_shadows_completed": self._ichi_post_exit_completed,
            }
        )

    @staticmethod
    def _threshold_key(prefix: str, threshold: float) -> str:
        return f"forensic_time_to_{prefix}_{threshold:.2f}r".replace(".", "p")

    def _forensic_diagnostics(self) -> dict[str, Any] | None:
        scenario = self.current_scenario
        if scenario is None or scenario.get("state") != ICHI_STATE:
            return None
        diagnostics = scenario.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            scenario["diagnostics"] = diagnostics
        return diagnostics

    def _update_actual_path(self, ts_event: int) -> None:
        diagnostics = self._forensic_diagnostics()
        if diagnostics is None or self.current_symbol is None:
            return
        scenario = self.current_scenario or {}
        side = int(scenario.get("side", 0))
        entry = float(scenario.get("entry_reference", 0.0))
        stop = float(scenario.get("stop", 0.0))
        if side not in (-1, 1) or not math.isfinite(entry) or entry <= 0.0:
            return
        risk_fraction = abs(entry - stop) / entry
        if not math.isfinite(risk_fraction) or risk_fraction <= 1e-12:
            return
        elapsed = max(0, self.minute_index - self.position_open_minute)
        bar = self.bars[self.current_symbol][-1]
        favourable_price = float(bar.high) if side > 0 else float(bar.low)
        adverse_price = float(bar.low) if side > 0 else float(bar.high)
        favourable_fraction = max(0.0, side * (favourable_price - entry) / entry)
        adverse_fraction = max(0.0, -side * (adverse_price - entry) / entry)
        close_r = side * (float(bar.close) - entry) / entry / risk_fraction
        mfe_r = favourable_fraction / risk_fraction
        mae_r = adverse_fraction / risk_fraction
        diagnostics["forensic_elapsed_minutes"] = elapsed
        diagnostics["forensic_close_r_latest"] = close_r
        if mfe_r > float(diagnostics.get("forensic_mfe_r", -math.inf)):
            diagnostics["forensic_mfe_r"] = mfe_r
            diagnostics["forensic_mfe_r_minute"] = elapsed
        if mae_r > float(diagnostics.get("forensic_mae_r", -math.inf)):
            diagnostics["forensic_mae_r"] = mae_r
            diagnostics["forensic_mae_r_minute"] = elapsed
        for threshold in self._MFE_THRESHOLDS_R:
            key = self._threshold_key("mfe", threshold)
            if key not in diagnostics and mfe_r >= threshold:
                diagnostics[key] = elapsed
        for threshold in self._MAE_THRESHOLDS_R:
            key = self._threshold_key("mae", threshold)
            if key not in diagnostics and mae_r >= threshold:
                diagnostics[key] = elapsed

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        active = bool(self._source_entry_signal_active())
        diagnostics["forensic_source_state_checks"] = int(
            diagnostics.get("forensic_source_state_checks", 0)
        ) + 1
        diagnostics["forensic_active_source_state_checks"] = int(
            diagnostics.get("forensic_active_source_state_checks", 0)
        ) + int(active)
        self.diagnostics["ichi_short_forensic_source_checks"] += 1
        self.diagnostics["ichi_short_forensic_active_source_checks"] += int(active)
        if not active and "forensic_first_source_state_loss_minute" not in diagnostics:
            diagnostics["forensic_first_source_state_loss_minute"] = elapsed
            diagnostics["forensic_mark_r_at_first_source_state_loss"] = close_r
            diagnostics["forensic_mfe_r_at_first_source_state_loss"] = float(
                diagnostics["forensic_mfe_r"]
            )
            diagnostics["forensic_mae_r_at_first_source_state_loss"] = float(
                diagnostics["forensic_mae_r"]
            )

        source_exit, snapshot = self._source_exit_signal()
        if source_exit and "forensic_source_exit_signal_minute" not in diagnostics:
            diagnostics["forensic_source_exit_signal_minute"] = elapsed
            diagnostics["forensic_mark_r_at_source_exit_signal"] = close_r
            diagnostics["forensic_mfe_r_at_source_exit_signal"] = float(
                diagnostics["forensic_mfe_r"]
            )
            diagnostics["forensic_mae_r_at_source_exit_signal"] = float(
                diagnostics["forensic_mae_r"]
            )
            for key, value in snapshot.items():
                diagnostics[f"forensic_{key}"] = value
            self._spawn_post_exit_shadow(ts_event, close_r)

    def _spawn_post_exit_shadow(self, ts_event: int, source_exit_mark_r: float) -> None:
        scenario = self.current_scenario
        if scenario is None or scenario.get("forensic_post_exit_shadow_spawned"):
            return
        symbol = self.current_symbol
        if symbol is None:
            return
        side = int(scenario.get("side", 0))
        entry = float(scenario.get("entry_reference", 0.0))
        stop = float(scenario.get("stop", 0.0))
        if side not in (-1, 1) or entry <= 0.0:
            return
        risk_fraction = abs(entry - stop) / entry
        cost = float(self.config.ichi_forensic_round_trip_cost_fraction)
        elapsed = max(0, self.minute_index - self.position_open_minute)
        roi_ratio = float(self._ichi_roi_profit_ratio(elapsed))
        roi_target = entry * (1.0 + side * roi_ratio)
        shadow = {
            "scenario_id": scenario.get("scenario_id"),
            "symbol": symbol,
            "side": side,
            "entry_reference": entry,
            "stop_reference": stop,
            "roi_target": roi_target,
            "risk_fraction": risk_fraction,
            "planned_loss_fraction": risk_fraction + cost,
            "source_exit_signal_ts": int(ts_event),
            "source_exit_elapsed_minutes": elapsed,
            "source_exit_mark_r": source_exit_mark_r,
            "remaining_horizon_minutes": max(0, 480 - elapsed),
            "post_exit_mfe_r": 0.0,
            "post_exit_mae_r": 0.0,
        }
        scenario["forensic_post_exit_shadow_spawned"] = True
        self._ichi_post_exit_active.append(shadow)
        self.diagnostics["ichi_short_post_exit_shadows_started"] += 1

    def _finalize_post_exit_shadow(
        self,
        shadow: dict[str, Any],
        *,
        ts_event: int,
        exit_price: float,
        reason: str,
        censored: bool = False,
    ) -> None:
        side = int(shadow["side"])
        entry = float(shadow["entry_reference"])
        cost = float(self.config.ichi_forensic_round_trip_cost_fraction)
        planned = float(shadow["planned_loss_fraction"])
        gross = side * (float(exit_price) - entry) / entry
        shadow.update(
            {
                "post_exit_resolution_ts": int(ts_event),
                "post_exit_elapsed_minutes": max(
                    0,
                    int(
                        (int(ts_event) - int(shadow["source_exit_signal_ts"]))
                        // 60_000_000_000
                    ),
                ),
                "post_exit_resolution_price": float(exit_price),
                "post_exit_resolution": reason,
                "post_exit_censored": bool(censored),
                "post_exit_gross_return_fraction": gross,
                "post_exit_net_return_fraction": gross - cost,
                "post_exit_net_r": (gross - cost) / max(planned, 1e-12),
            }
        )
        self._ichi_post_exit_completed.append(shadow)

    def _update_post_exit_shadows(self, ts_event: int) -> None:
        if not self._ichi_post_exit_active:
            return
        remaining: list[dict[str, Any]] = []
        for shadow in self._ichi_post_exit_active:
            symbol = str(shadow["symbol"])
            if not self.bars[symbol]:
                remaining.append(shadow)
                continue
            elapsed_after = max(
                0,
                int(
                    (int(ts_event) - int(shadow["source_exit_signal_ts"]))
                    // 60_000_000_000
                ),
            )
            if elapsed_after <= 0:
                remaining.append(shadow)
                continue
            side = int(shadow["side"])
            entry = float(shadow["entry_reference"])
            stop = float(shadow["stop_reference"])
            roi_target = float(shadow["roi_target"])
            risk = float(shadow["risk_fraction"])
            bar = self.bars[symbol][-1]
            high, low, close = float(bar.high), float(bar.low), float(bar.close)
            favourable = high if side > 0 else low
            adverse = low if side > 0 else high
            shadow["post_exit_mfe_r"] = max(
                float(shadow["post_exit_mfe_r"]),
                max(0.0, side * (favourable - entry) / entry) / risk,
            )
            shadow["post_exit_mae_r"] = max(
                float(shadow["post_exit_mae_r"]),
                max(0.0, -side * (adverse - entry) / entry) / risk,
            )
            stop_hit = low <= stop if side > 0 else high >= stop
            roi_hit = high >= roi_target if side > 0 else low <= roi_target
            # Conservative same-minute ordering: the adverse source stop wins.
            if stop_hit:
                self._finalize_post_exit_shadow(
                    shadow,
                    ts_event=ts_event,
                    exit_price=stop,
                    reason="ORIGINAL_STOP",
                )
                continue
            if roi_hit:
                self._finalize_post_exit_shadow(
                    shadow,
                    ts_event=ts_event,
                    exit_price=roi_target,
                    reason="ORIGINAL_ROI",
                )
                continue
            if elapsed_after >= int(shadow["remaining_horizon_minutes"]):
                self._finalize_post_exit_shadow(
                    shadow,
                    ts_event=ts_event,
                    exit_price=close,
                    reason="ORIGINAL_HORIZON",
                )
                continue
            if int(ts_event) >= int(self.config.evaluation_end_ns):
                self._finalize_post_exit_shadow(
                    shadow,
                    ts_event=ts_event,
                    exit_price=close,
                    reason="EVALUATION_END",
                    censored=True,
                )
                continue
            remaining.append(shadow)
        self._ichi_post_exit_active = remaining
        self.diagnostics["ichi_short_post_exit_shadows_completed"] = (
            self._ichi_post_exit_completed
        )

    def _manage_open_position(self, ts_event: int) -> None:
        self._update_actual_path(ts_event)
        super()._manage_open_position(ts_event)

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self._update_post_exit_shadows(ts_event)
        super()._on_complete_universe_minute(ts_event)

    def on_position_closed(self, event: Any) -> None:
        diagnostics = self._forensic_diagnostics()
        if diagnostics is not None:
            checks = int(diagnostics.get("forensic_source_state_checks", 0))
            active = int(diagnostics.get("forensic_active_source_state_checks", 0))
            diagnostics["forensic_active_source_state_ratio"] = (
                active / checks if checks > 0 else 0.0
            )
        super().on_position_closed(event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
