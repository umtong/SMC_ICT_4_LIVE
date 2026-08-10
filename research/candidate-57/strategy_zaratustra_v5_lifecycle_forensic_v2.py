"""Behaviour-preserving lifecycle instrumentation for public ZaratustraV5.

This wrapper does not change entries, sizing, stop, target, trailing, max-hold,
funding handling, arbitration, or fills. It records the causal path that leads
from source entry to trailing winner, max-hold exit, or source stop so the next
change can be predicted before it is tested.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from router import FeatureObservation, ZARA_STATE, route_symbol
from strategy_zaratustra_source_base import (
    Candidate35Config as _BaseConfig,
    Candidate35Strategy as _BaseStrategy,
)


class Candidate35Config(_BaseConfig, frozen=True):
    """No policy parameters are added: this is a diagnostic-only wrapper."""


class Candidate35Strategy(_BaseStrategy):
    _SNAPSHOT_MINUTES = (5, 15, 30, 60, 120, 240, 360, 480)
    _MFE_THRESHOLDS_R = (0.10, 0.24, 0.50, 1.00)
    _MAE_THRESHOLDS_R = (0.10, 0.25, 0.50, 0.75)

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.diagnostics.update(
            {
                "candidate57_zara_lifecycle_forensic_v2": 1,
                "zara_forensic_policy_changed": 0,
                "zara_forensic_source_state_checks": 0,
                "zara_forensic_same_side_checks": 0,
                "zara_forensic_opposite_side_checks": 0,
            }
        )

    def _forensic_diagnostics(self) -> dict[str, Any] | None:
        scenario = self.current_scenario
        if scenario is None or scenario.get("state") != ZARA_STATE:
            return None
        diagnostics = scenario.get("diagnostics")
        if not isinstance(diagnostics, dict):
            diagnostics = {}
            scenario["diagnostics"] = diagnostics
        return diagnostics

    @staticmethod
    def _threshold_key(prefix: str, threshold: float) -> str:
        text = f"{threshold:.2f}".replace(".", "p")
        return f"forensic_time_to_{prefix}_{text}r"

    def _update_lifecycle(self, ts_event: int) -> None:
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

        bar = self.bars[self.current_symbol][-1]
        elapsed = max(0, self.minute_index - self.position_open_minute)
        favourable_price = float(bar.high) if side > 0 else float(bar.low)
        adverse_price = float(bar.low) if side > 0 else float(bar.high)
        favourable_fraction = max(0.0, side * (favourable_price - entry) / entry)
        adverse_fraction = max(0.0, -side * (adverse_price - entry) / entry)
        close_r = side * (float(bar.close) - entry) / entry / risk_fraction
        mfe_r = favourable_fraction / risk_fraction
        mae_r = adverse_fraction / risk_fraction

        diagnostics["forensic_elapsed_minutes"] = elapsed
        diagnostics["forensic_close_r_latest"] = close_r
        previous_mfe = float(diagnostics.get("forensic_mfe_r", -math.inf))
        previous_mae = float(diagnostics.get("forensic_mae_r", -math.inf))
        if mfe_r > previous_mfe:
            diagnostics["forensic_mfe_r"] = mfe_r
            diagnostics["forensic_mfe_r_minute"] = elapsed
        if mae_r > previous_mae:
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

        if elapsed in self._SNAPSHOT_MINUTES:
            diagnostics[f"forensic_mark_r_{elapsed}m"] = close_r
            diagnostics[f"forensic_mfe_r_{elapsed}m"] = float(
                diagnostics["forensic_mfe_r"]
            )
            diagnostics[f"forensic_mae_r_{elapsed}m"] = float(
                diagnostics["forensic_mae_r"]
            )
            diagnostics[f"forensic_trail_active_{elapsed}m"] = int(
                bool(self._trail_active)
            )

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 5 != 4:
            return
        decision = route_symbol(
            self.current_symbol,
            tuple(self.bars[self.current_symbol]),
            FeatureObservation(ts_event, ready=True),
            self.route_config,
        )
        same_side = bool(decision.actionable and int(decision.side) == side)
        opposite_side = bool(decision.actionable and int(decision.side) == -side)
        diagnostics["forensic_source_state_checks"] = int(
            diagnostics.get("forensic_source_state_checks", 0)
        ) + 1
        diagnostics["forensic_same_side_checks"] = int(
            diagnostics.get("forensic_same_side_checks", 0)
        ) + int(same_side)
        diagnostics["forensic_opposite_side_checks"] = int(
            diagnostics.get("forensic_opposite_side_checks", 0)
        ) + int(opposite_side)
        self.diagnostics["zara_forensic_source_state_checks"] += 1
        self.diagnostics["zara_forensic_same_side_checks"] += int(same_side)
        self.diagnostics["zara_forensic_opposite_side_checks"] += int(opposite_side)

        if not same_side and "forensic_first_source_invalidation_minute" not in diagnostics:
            diagnostics["forensic_first_source_invalidation_minute"] = elapsed
            diagnostics["forensic_mark_r_at_first_source_invalidation"] = close_r
            diagnostics["forensic_mfe_r_at_first_source_invalidation"] = float(
                diagnostics["forensic_mfe_r"]
            )
            diagnostics["forensic_mae_r_at_first_source_invalidation"] = float(
                diagnostics["forensic_mae_r"]
            )
        if opposite_side and "forensic_first_opposite_state_minute" not in diagnostics:
            diagnostics["forensic_first_opposite_state_minute"] = elapsed
            diagnostics["forensic_mark_r_at_first_opposite_state"] = close_r

        live = decision.diagnostics or {}
        for label in ("5m", "15m", "30m"):
            for field in ("rsi", "plus_di", "minus_di", "bb_middle", "close"):
                value = live.get(f"{field}_{label}")
                if value is not None:
                    diagnostics[f"forensic_latest_{field}_{label}"] = value

    def _pretag_management_exit(self, ts_event: int) -> None:
        if self.current_scenario is None or self.current_symbol is None:
            return
        if self.current_scenario.get("state") != ZARA_STATE:
            return
        side = int(self.current_scenario.get("side", 0))
        entry = float(self.current_scenario.get("entry_reference", 0.0))
        bar = self.bars[self.current_symbol][-1]
        elapsed = max(0, self.minute_index - self.position_open_minute)

        if self._trail_active and self._trail_best is not None and side in (-1, 1):
            distance = float(self.route_config.picasso_trailing_positive)
            trailing_stop = float(self._trail_best) * (1.0 - side * distance)
            hit = (
                float(bar.low) <= trailing_stop
                if side > 0
                else float(bar.high) >= trailing_stop
            )
            if hit:
                self.current_scenario["management_exit_reason"] = (
                    "PUBLIC_ZARATUSTRA_TRAILING"
                )
                return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        before_funding = (
            moment.hour in (7, 15, 23)
            and moment.minute >= self.config.funding_flatten_minute
        )
        if before_funding:
            self.current_scenario["management_exit_reason"] = "FUNDING_FLATTEN"
        elif elapsed >= int(self.config.max_hold_minutes):
            self.current_scenario["management_exit_reason"] = "MAX_HOLD"
        elif ts_event >= int(self.config.evaluation_end_ns):
            self.current_scenario["management_exit_reason"] = "EVALUATION_END"
        elif side not in (-1, 1) or not math.isfinite(entry) or entry <= 0.0:
            self.current_scenario["management_exit_reason"] = "INVALID_SCENARIO"

    def _manage_open_position(self, ts_event: int) -> None:
        self._update_lifecycle(ts_event)
        self._pretag_management_exit(ts_event)
        was_active = bool(self._trail_active)
        super()._manage_open_position(ts_event)
        diagnostics = self._forensic_diagnostics()
        if (
            diagnostics is not None
            and not was_active
            and bool(self._trail_active)
            and "forensic_trailing_activation_minute" not in diagnostics
        ):
            diagnostics["forensic_trailing_activation_minute"] = max(
                0, self.minute_index - self.position_open_minute
            )

    def on_position_closed(self, event: Any) -> None:
        if (
            self.current_scenario is not None
            and self.current_scenario.get("state") == ZARA_STATE
        ):
            diagnostics = self._forensic_diagnostics()
            if diagnostics is not None:
                checks = int(diagnostics.get("forensic_source_state_checks", 0))
                same = int(diagnostics.get("forensic_same_side_checks", 0))
                diagnostics["forensic_same_side_state_ratio"] = (
                    same / checks if checks > 0 else 0.0
                )
            self.current_scenario.setdefault(
                "management_exit_reason", "SOURCE_STOP_OR_BRACKET"
            )
        super().on_position_closed(event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
