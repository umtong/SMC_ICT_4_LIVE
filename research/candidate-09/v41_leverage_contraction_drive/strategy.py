"""Candidate 09 v41: leverage-contraction drive, delayed hold, first retest.

A completed five-minute price drive owns no trade by itself. Five minutes later,
when the corresponding Binance open-interest observation becomes available, the
strategy classifies the drive as a leverage reset only when open interest
contracted and the contemporaneous premium did not expand in the drive
direction. Price must have remained outside the pre-drive balance throughout
the observation delay. That completed state arms the first later retest of the
same boundary; inherited completed-auction liquidity owns the target.

The exact control removes only open-interest/premium reset evidence. Context,
price drive, delayed residence, entry, invalidation, objective, costs, risk and
NautilusTrader execution remain identical.
"""
from __future__ import annotations

from bisect import bisect_right
import math
from pathlib import Path
from typing import Any

from nautilus_trader.model.data import Bar

from strategy_base import PendingSetup
from strategy_v35 import Candidate16Config as _Candidate35Config
from strategy_v35 import Candidate16Strategy as _Candidate35Strategy


_MINUTE_NS = 60_000_000_000
_METRICS_PUBLICATION_DELAY_MINUTES = 5
_EVENT_MINUTES = 5


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if math.isfinite(number) else math.nan


class Candidate16Config(_Candidate35Config, frozen=True):
    candidate41_require_leverage_reset: bool = True
    candidate41_event_minutes: int = _EVENT_MINUTES
    candidate41_publication_delay_minutes: int = _METRICS_PUBLICATION_DELAY_MINUTES


class Candidate16Strategy(_Candidate35Strategy):
    """Causal five-minute leverage-reset continuation on the verified runner."""

    def __init__(self, config: Candidate16Config) -> None:
        super().__init__(config=config)
        if config.candidate41_event_minutes != _EVENT_MINUTES:
            raise ValueError("v41 pre-registration fixes the drive interval at five minutes")
        if (
            config.candidate41_publication_delay_minutes
            != _METRICS_PUBLICATION_DELAY_MINUTES
        ):
            raise ValueError("v41 must honor the full five-minute metrics delay")
        self._candidate41_feature_times: list[int] = []
        self._candidate41_last_metrics_observed_ns = -1
        self.diagnostics.update(
            {
                "candidate41_metrics_observations": 0,
                "candidate41_price_drives": 0,
                "candidate41_leverage_reset_passes": 0,
                "candidate41_leverage_reset_blocks": 0,
                "candidate41_delayed_residence_passes": 0,
                "candidate41_delayed_residence_blocks": 0,
                "candidate41_retests_armed": 0,
                "candidate41_price_only_control_paths": 0,
            }
        )

    def _load_features(self, path: Path) -> None:
        super()._load_features(path)
        self._candidate41_feature_times = [
            int(record["observed_time_ns"]) for record in self.features
        ]

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        """V41 does not trade inherited boundary sweeps; pools remain objectives."""
        del row, previous_close

    def on_bar(self, bar: Bar) -> None:
        super().on_bar(bar)
        self._candidate41_maybe_arm_reset()

    def _candidate41_row_at_or_before(
        self,
        ts_event: int,
    ) -> dict[str, float | int] | None:
        for row in reversed(self.bars):
            if int(row["ts"]) <= ts_event:
                return row
        return None

    def _candidate41_feature_at_or_before(
        self,
        ts_event: int,
    ) -> dict[str, Any] | None:
        index = bisect_right(self._candidate41_feature_times, ts_event) - 1
        return self.features[index] if index >= 0 else None

    def _candidate41_rows_between(
        self,
        start_exclusive: int,
        end_inclusive: int,
    ) -> list[dict[str, float | int]]:
        return [
            row
            for row in self.bars
            if start_exclusive < int(row["ts"]) <= end_inclusive
        ]

    def _candidate41_close_no_trade(
        self,
        *,
        scenario_id: str,
        row: dict[str, float | int],
        reason: str,
        details: dict[str, Any],
    ) -> None:
        self._transition(
            scenario_id,
            "LEVERAGE_RESET_NO_TRADE",
            int(row["ts"]),
            int(row["ts"]),
            "CLOSED",
            reason,
            float(row["close"]),
            details,
        )

    def _candidate41_maybe_arm_reset(self) -> None:
        if not self.bars:
            return
        row = self.bars[-1]
        ts_event = int(row["ts"])
        if not self.portfolio.is_flat(self.config.instrument_id):
            return
        if self.entry_pending or self.pending is not None:
            return
        if not self._in_evaluation(ts_event) or self._funding_blackout(ts_event):
            return
        if not self._features_ready(ts_event):
            return
        if self.bar_index - self.last_entry_index < self.config.cooldown_bars:
            return
        feature = self.current_feature
        if feature is None or not bool(feature.get("metrics_ready", False)):
            return
        try:
            metrics_observed_ns = int(feature.get("metrics_observed_time_ns"))
        except (TypeError, ValueError, OverflowError):
            return
        if metrics_observed_ns > ts_event:
            raise RuntimeError("future metrics observation reached v41")
        if metrics_observed_ns <= self._candidate41_last_metrics_observed_ns:
            return
        self._candidate41_last_metrics_observed_ns = metrics_observed_ns
        self.diagnostics["candidate41_metrics_observations"] = int(
            self.diagnostics["candidate41_metrics_observations"]
        ) + 1

        delay_ns = self.config.candidate41_publication_delay_minutes * _MINUTE_NS
        event_ns = self.config.candidate41_event_minutes * _MINUTE_NS
        event_end_ns = metrics_observed_ns - delay_ns
        event_start_ns = event_end_ns - event_ns
        event_start = self._candidate41_row_at_or_before(event_start_ns)
        event_end = self._candidate41_row_at_or_before(event_end_ns)
        if event_start is None or event_end is None:
            return
        event_rows = self._candidate41_rows_between(
            int(event_start["ts"]),
            event_end_ns,
        )
        delay_rows = self._candidate41_rows_between(event_end_ns, ts_event)
        pre_rows = [
            row for row in self.bars if int(row["ts"]) < int(event_start["ts"])
        ][-self.config.structure_lookback_bars :]
        if (
            len(event_rows) != self.config.candidate41_event_minutes
            or len(delay_rows) < self.config.candidate41_publication_delay_minutes
            or len(pre_rows) < self.config.structure_lookback_bars
        ):
            return
        atr = self._atr()
        if not math.isfinite(atr) or atr <= 0.0:
            return

        start_close = float(event_start["close"])
        end_close = float(event_end["close"])
        net = end_close - start_close
        if abs(net) <= 1e-12:
            return
        direction = 1 if net > 0.0 else -1
        closes = [start_close, *[float(item["close"]) for item in event_rows]]
        travelled = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        efficiency = abs(net) / travelled if travelled > 0.0 else 0.0
        progress_atr = direction * net / atr
        boundary = (
            max(float(item["high"]) for item in pre_rows)
            if direction > 0
            else min(float(item["low"]) for item in pre_rows)
        )
        broke_balance = end_close > boundary if direction > 0 else end_close < boundary
        if not (
            broke_balance
            and progress_atr >= self.config.router_acceptance_min_progress_atr
            and efficiency >= self.config.router_acceptance_min_efficiency
        ):
            return
        self.diagnostics["candidate41_price_drives"] = int(
            self.diagnostics["candidate41_price_drives"]
        ) + 1
        self.scenario_counter += 1
        scenario_id = f"lr41-{self.scenario_counter:07d}"

        residence_pass = all(
            float(item["close"]) > boundary
            if direction > 0
            else float(item["close"]) < boundary
            for item in delay_rows
        )
        event_feature = self._candidate41_feature_at_or_before(event_end_ns)
        premium_change = (
            _finite(event_feature.get("premium_change_5m"))
            if event_feature is not None
            and bool(event_feature.get("basis_ready", False))
            else math.nan
        )
        oi_change = _finite(feature.get("oi_change_5m"))
        leverage_reset = (
            math.isfinite(oi_change)
            and math.isfinite(premium_change)
            and oi_change < 0.0
            and direction * premium_change <= 0.0
        )
        details = {
            "candidate41_require_leverage_reset": (
                self.config.candidate41_require_leverage_reset
            ),
            "candidate41_metrics_observed_ns": metrics_observed_ns,
            "candidate41_event_start_ns": event_start_ns,
            "candidate41_event_end_ns": event_end_ns,
            "candidate41_publication_delay_minutes": (
                self.config.candidate41_publication_delay_minutes
            ),
            "candidate41_direction": direction,
            "candidate41_start_close": start_close,
            "candidate41_end_close": end_close,
            "candidate41_balance_boundary": boundary,
            "candidate41_progress_atr": progress_atr,
            "candidate41_path_efficiency": efficiency,
            "candidate41_oi_change_5m": oi_change,
            "candidate41_premium_change_5m": premium_change,
            "candidate41_leverage_reset": leverage_reset,
            "candidate41_delayed_residence": residence_pass,
        }
        if not residence_pass:
            self.diagnostics["candidate41_delayed_residence_blocks"] = int(
                self.diagnostics["candidate41_delayed_residence_blocks"]
            ) + 1
            self._candidate41_close_no_trade(
                scenario_id=scenario_id,
                row=row,
                reason="PRICE_REENTERED_PRE_DRIVE_BALANCE_DURING_METRICS_DELAY",
                details=details,
            )
            return
        self.diagnostics["candidate41_delayed_residence_passes"] = int(
            self.diagnostics["candidate41_delayed_residence_passes"]
        ) + 1
        if self.config.candidate41_require_leverage_reset and not leverage_reset:
            self.diagnostics["candidate41_leverage_reset_blocks"] = int(
                self.diagnostics["candidate41_leverage_reset_blocks"]
            ) + 1
            self._candidate41_close_no_trade(
                scenario_id=scenario_id,
                row=row,
                reason="PRICE_DRIVE_WAS_NOT_OPEN_INTEREST_AND_PREMIUM_CONTRACTION",
                details=details,
            )
            return
        if leverage_reset:
            self.diagnostics["candidate41_leverage_reset_passes"] = int(
                self.diagnostics["candidate41_leverage_reset_passes"]
            ) + 1
        else:
            self.diagnostics["candidate41_price_only_control_paths"] = int(
                self.diagnostics["candidate41_price_only_control_paths"]
            ) + 1

        self.pending = PendingSetup(
            scenario_id=scenario_id,
            branch="ACCEPTANCE",
            side=direction,
            swept_kind="HIGH" if direction > 0 else "LOW",
            pool_id=f"leverage-reset-{metrics_observed_ns}",
            pool_level=boundary,
            created_index=self.bar_index,
            expires_index=self.bar_index + self.config.acceptance_retrace_bars,
            sweep_extreme=(
                max(float(item["high"]) for item in event_rows + delay_rows)
                if direction > 0
                else min(float(item["low"]) for item in event_rows + delay_rows)
            ),
            structure=boundary,
            atr=atr,
            hold_count=self.config.acceptance_min_hold_bars,
            retrace_armed=True,
            details=details,
        )
        self.diagnostics["candidate41_retests_armed"] = int(
            self.diagnostics["candidate41_retests_armed"]
        ) + 1
        self._transition(
            scenario_id,
            "LEVERAGE_CONTRACTION_DRIVE_CONFIRMED",
            event_end_ns,
            metrics_observed_ns,
            "FIRST_RETEST_ARMED",
            "FIVE_MINUTE_DRIVE_HELD_THROUGH_FULL_METRICS_PUBLICATION_DELAY",
            float(row["close"]),
            details,
        )


__all__ = ["Candidate16Config", "Candidate16Strategy"]
