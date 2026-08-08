"""Causal shock classification and watch creation for Candidate 09 v50."""
from __future__ import annotations

from bisect import bisect_right
import math
from pathlib import Path
from typing import Any

from nautilus_trader.model.data import Bar

from v50_types import DeleveragingWatch, EVENT_MINUTES, MINUTE_NS, PUBLICATION_DELAY_MINUTES, finite


class Candidate50StateMixin:
    def __init__(self, config: Any) -> None:
        super().__init__(config=config)
        if config.candidate50_event_minutes != EVENT_MINUTES:
            raise ValueError("v50 fixes the shock interval at five minutes")
        if config.candidate50_publication_delay_minutes != PUBLICATION_DELAY_MINUTES:
            raise ValueError("v50 must honor the full five-minute metrics delay")
        self._candidate50_feature_times: list[int] = []
        self._candidate50_last_metrics_observed_ns = -1
        self._candidate50_watch: DeleveragingWatch | None = None
        self.diagnostics.update({
            "candidate50_metrics_observations": 0,
            "candidate50_price_shocks": 0,
            "candidate50_directional_flow_shocks": 0,
            "candidate50_forced_deleveraging_passes": 0,
            "candidate50_forced_deleveraging_blocks": 0,
            "candidate50_price_flow_control_paths": 0,
            "candidate50_delay_effort_without_result": 0,
            "candidate50_delay_extension_blocks": 0,
            "candidate50_target_consumed_before_trigger": 0,
            "candidate50_watches_armed": 0,
            "candidate50_watches_expired": 0,
            "candidate50_new_discovery_invalidations": 0,
            "candidate50_reversal_initiatives": 0,
            "candidate50_poc_migration_blocks": 0,
            "candidate50_geometry_rejections": 0,
            "candidate50_entries_submitted": 0,
        })

    def _load_features(self, path: Path) -> None:
        super()._load_features(path)
        self._candidate50_feature_times = [
            int(record["observed_time_ns"]) for record in self.features
        ]

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        """Completed-auction pools remain context only; V50 owns its entry cause."""
        del row, previous_close

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._candidate50_watch = None

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        if not self.bars:
            return
        row = self.bars[-1]
        if not self.portfolio.is_flat(self.config.instrument_id) or self.entry_pending:
            return
        if self._candidate50_watch is not None:
            self._candidate50_process_watch(row)
            if self._candidate50_watch is not None or self.entry_pending:
                return
        self._candidate50_maybe_arm(row)

    def _candidate50_row_at_or_before(self, ts_event: int):
        for row in reversed(self.bars):
            if int(row["ts"]) <= ts_event:
                return row
        return None

    def _candidate50_rows_between(self, start_exclusive: int, end_inclusive: int):
        return [
            row for row in self.bars
            if start_exclusive < int(row["ts"]) <= end_inclusive
        ]

    def _candidate50_feature_at_or_before(self, ts_event: int):
        index = bisect_right(self._candidate50_feature_times, ts_event) - 1
        return self.features[index] if index >= 0 else None

    def _candidate50_features_for_rows(self, rows: list[dict[str, float | int]]):
        return [
            feature
            for row in rows
            if (feature := self._candidate50_feature_at_or_before(int(row["ts"])))
            is not None
        ]

    def _candidate50_maybe_arm(self, row: dict[str, float | int]) -> None:
        ts_event = int(row["ts"])
        if (
            not self._in_evaluation(ts_event)
            or self._funding_blackout(ts_event)
            or not self._features_ready(ts_event)
            or self.pending is not None
            or self.bar_index - self.last_entry_index < self.config.cooldown_bars
        ):
            return
        feature = self.current_feature
        if feature is None or not bool(feature.get("metrics_ready", False)):
            return
        try:
            metrics_observed_ns = int(feature.get("metrics_observed_time_ns"))
        except (TypeError, ValueError, OverflowError):
            return
        if metrics_observed_ns > ts_event:
            raise RuntimeError("future positioning observation reached v50")
        if metrics_observed_ns <= self._candidate50_last_metrics_observed_ns:
            return
        self._candidate50_last_metrics_observed_ns = metrics_observed_ns
        self.diagnostics["candidate50_metrics_observations"] += 1

        delay_ns = self.config.candidate50_publication_delay_minutes * MINUTE_NS
        event_ns = self.config.candidate50_event_minutes * MINUTE_NS
        event_end_ns = metrics_observed_ns - delay_ns
        event_start_ns = event_end_ns - event_ns
        event_start = self._candidate50_row_at_or_before(event_start_ns)
        event_end = self._candidate50_row_at_or_before(event_end_ns)
        if event_start is None or event_end is None:
            return
        event_rows = self._candidate50_rows_between(int(event_start["ts"]), event_end_ns)
        delay_rows = self._candidate50_rows_between(event_end_ns, ts_event)
        pre_rows = [
            item for item in self.bars if int(item["ts"]) < int(event_start["ts"])
        ][-self.config.structure_lookback_bars :]
        if (
            len(event_rows) != self.config.candidate50_event_minutes
            or len(delay_rows) < self.config.candidate50_publication_delay_minutes
            or len(pre_rows) < self.config.structure_lookback_bars
        ):
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return

        start_close, end_close = float(event_start["close"]), float(event_end["close"])
        net = end_close - start_close
        if abs(net) <= 1e-12:
            return
        direction = 1 if net > 0.0 else -1
        reversal_side = -direction
        closes = [start_close, *[float(item["close"]) for item in event_rows]]
        travelled = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
        efficiency = abs(net) / travelled if travelled > 0.0 else 0.0
        progress_atr = direction * net / atr
        boundary = (
            max(float(item["high"]) for item in pre_rows)
            if direction > 0 else min(float(item["low"]) for item in pre_rows)
        )
        broke_balance = end_close > boundary if direction > 0 else end_close < boundary
        if not (
            broke_balance
            and progress_atr >= self.config.router_acceptance_min_progress_atr
            and efficiency >= self.config.router_acceptance_min_efficiency
        ):
            return
        self.diagnostics["candidate50_price_shocks"] += 1

        event_features = self._candidate50_features_for_rows(event_rows)
        event_flow_bars = sum(
            direction * finite(item.get("flow_60s"))
            >= self.config.router_min_directional_effort
            for item in event_features
        )
        event_burst = max(
            (finite(item.get("notional_burst")) for item in event_features),
            default=math.nan,
        )
        if (
            event_flow_bars < self.config.router_acceptance_min_outside_closes
            or not math.isfinite(event_burst)
            or event_burst < self.config.sweep_min_notional_burst
        ):
            return
        self.diagnostics["candidate50_directional_flow_shocks"] += 1

        event_feature = self._candidate50_feature_at_or_before(event_end_ns)
        if event_feature is None or not bool(event_feature.get("basis_ready", False)):
            return
        premium_change = finite(event_feature.get("premium_change_5m"))
        event_poc = finite(event_feature.get("footprint_poc_price"))
        oi_change = finite(feature.get("oi_change_5m"))
        if not all(math.isfinite(v) for v in (premium_change, event_poc, oi_change)):
            return
        forced_deleveraging = oi_change < 0.0 and direction * premium_change > 0.0
        if self.config.candidate50_require_forced_deleveraging:
            if not forced_deleveraging:
                self.diagnostics["candidate50_forced_deleveraging_blocks"] += 1
                return
            self.diagnostics["candidate50_forced_deleveraging_passes"] += 1
        else:
            self.diagnostics["candidate50_price_flow_control_paths"] += 1

        event_extreme = (
            max(float(item["high"]) for item in event_rows)
            if direction > 0 else min(float(item["low"]) for item in event_rows)
        )
        full_extreme = (
            max(event_extreme, max(float(item["high"]) for item in delay_rows))
            if direction > 0
            else min(event_extreme, min(float(item["low"]) for item in delay_rows))
        )
        extension_atr = direction * (full_extreme - event_extreme) / atr
        delay_features = self._candidate50_features_for_rows(delay_rows)
        delay_flow_bars = sum(
            direction * finite(item.get("flow_60s"))
            >= self.config.router_min_directional_effort
            for item in delay_features
        )
        still_outside = direction * (float(row["close"]) - boundary) > 0.0
        if (
            extension_atr > self.config.router_failed_max_progress_atr
            or delay_flow_bars < self.config.router_acceptance_min_outside_closes
            or not still_outside
        ):
            self.diagnostics["candidate50_delay_extension_blocks"] += 1
            return
        self.diagnostics["candidate50_delay_effort_without_result"] += 1

        reversal_structure = (
            min(float(item["low"]) for item in delay_rows)
            if reversal_side < 0 else max(float(item["high"]) for item in delay_rows)
        )
        self.scenario_counter += 1
        scenario_id = f"dx50-{self.scenario_counter:07d}"
        details = {
            "candidate50_require_forced_deleveraging": self.config.candidate50_require_forced_deleveraging,
            "candidate50_metrics_observed_ns": metrics_observed_ns,
            "candidate50_event_start_ns": event_start_ns,
            "candidate50_event_end_ns": event_end_ns,
            "candidate50_direction": direction,
            "candidate50_reversal_side": reversal_side,
            "candidate50_balance_boundary": boundary,
            "candidate50_shock_progress_atr": progress_atr,
            "candidate50_shock_efficiency": efficiency,
            "candidate50_event_directional_flow_bars": event_flow_bars,
            "candidate50_delay_directional_flow_bars": delay_flow_bars,
            "candidate50_event_notional_burst": event_burst,
            "candidate50_oi_change_5m": oi_change,
            "candidate50_premium_change_5m": premium_change,
            "candidate50_forced_deleveraging": forced_deleveraging,
            "candidate50_extension_atr_during_delay": extension_atr,
            "candidate50_shock_extreme": full_extreme,
            "candidate50_event_poc": event_poc,
            "candidate50_reversal_structure": reversal_structure,
        }
        self._candidate50_watch = DeleveragingWatch(
            scenario_id=scenario_id,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            shock_direction=direction,
            reversal_side=reversal_side,
            boundary=boundary,
            shock_extreme=full_extreme,
            reversal_structure=reversal_structure,
            event_poc=event_poc,
            atr=atr,
            details=details,
        )
        self.diagnostics["candidate50_watches_armed"] += 1
        self._transition(
            scenario_id,
            "FORCED_DELEVERAGING_EXHAUSTION_CONFIRMED",
            ts_event,
            ts_event,
            "OPPOSITE_INITIATIVE_WATCH",
            "DIRECTIONAL_FLOW_FAILED_TO_EXTEND_FOR_FULL_METRICS_DELAY",
            float(row["close"]),
            details,
        )
