"""Mechanism-preserving repair experiments for the public Picasso family.

The public system and Candidate 51 replay both show the same anatomy:

* a high-frequency trailing/ROI winner engine;
* a much smaller set of very large source-signal, emergency-stop and timeout
  losses.

This module does not treat positive/negative final PnL as a verdict and does not
replace the entry policy with a generic filter.  It keeps the public entry and
winner management intact, then isolates three different questions:

1. Is the remote source stop merely a sizing-geometry mismatch?
2. Does the far EMA/ATR source exit protect the account or create the large-loss
   tail?
3. Can a causal loss-of-thesis transition or progress failure exit reduce that
   tail without destroying the observed trailing winner engine?

Every experiment still uses the shared NautilusTrader account, current-NAV 3%
planned-loss sizing, actual-fill validity, one global slot, fees, slippage and
funding reserve.
"""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any

import router as _router
import strategy_picasso as _base


class Candidate35Config(_base.Candidate35Config, frozen=True):
    # Source-contract compatibility: StrategyConfig is a msgspec Struct rather
    # than a dataclass, while the already-committed workflow probes this mapping.
    __dataclass_fields__ = {
        "picasso_stop_mode": None,
        "picasso_management_mode": None,
        "picasso_stop_atr_buffer": None,
        "picasso_progress_checkpoint_1_minutes": None,
    }

    # source | signal_extreme_atr | midline_atr
    picasso_stop_mode: str = "source"
    picasso_stop_atr_buffer: float = 0.25

    # source | no_source_exit | lifecycle | lifecycle_progress
    picasso_management_mode: str = "source"

    # Scale-free progress tests are expressed as fractions of the public
    # trailing activation distance, not arbitrary return targets.
    picasso_progress_checkpoint_1_minutes: int = 120
    picasso_progress_checkpoint_2_minutes: int = 240
    picasso_progress_checkpoint_3_minutes: int = 480
    picasso_progress_mfe_fraction_1: float = 0.50
    picasso_progress_mfe_fraction_2: float = 0.75
    picasso_progress_mfe_fraction_3: float = 1.00


class Candidate35Strategy(_base.Candidate35Strategy):
    """Picasso entry/winner engine with separately testable loss engines."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self._repair_exit_pending = False
        self._repair_entry = math.nan
        self.diagnostics.update(
            {
                "picasso_repair_stop_mode": str(config.picasso_stop_mode),
                "picasso_repair_management_mode": str(config.picasso_management_mode),
                "picasso_repair_stop_submissions": 0,
                "picasso_repair_stop_unavailable": 0,
                "picasso_repair_lifecycle_exits": 0,
                "picasso_repair_progress_exits": 0,
                "picasso_repair_path_updates": 0,
                "picasso_repair_exit_counts": {},
            }
        )

    def _reset_repair_state(self) -> None:
        self._repair_exit_pending = False
        self._repair_entry = math.nan

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()
        self._reset_repair_state()

    def _after_position_opened(self, event: Any, scenario: dict[str, Any]) -> None:
        del event
        raw = scenario.get("actual_entry_fill", scenario.get("entry_reference"))
        try:
            entry = float(raw)
        except (TypeError, ValueError):
            entry = math.nan
        self._repair_entry = entry if math.isfinite(entry) and entry > 0.0 else math.nan
        self._repair_exit_pending = False
        scenario.update(
            {
                "picasso_repair_mfe_fraction": 0.0,
                "picasso_repair_mae_fraction": 0.0,
                "picasso_repair_current_fraction": 0.0,
                "picasso_repair_elapsed_minutes": 0,
                "picasso_repair_trail_activation_minutes": None,
                "picasso_repair_exit_driver": None,
            }
        )

    def _after_position_closed(self, event: Any, record: dict[str, Any]) -> None:
        del event, record
        self._reset_repair_state()

    def _completed_candles(self, symbol: str):
        return _router._aggregate_complete(
            tuple(self.bars[symbol]),
            int(self.route_config.picasso_bucket_minutes),
        )

    def _repaired_stop(self, decision, ts_event: int) -> tuple[float, dict[str, float | str]] | None:
        mode = str(self.config.picasso_stop_mode).strip().lower()
        if mode == "source":
            return float(decision.stop_reference), {
                "stop_mode": mode,
                "source_stop": float(decision.stop_reference),
                "repaired_stop": float(decision.stop_reference),
            }
        if mode not in {"signal_extreme_atr", "midline_atr"}:
            self._event(
                "PICASSO_REPAIR_STOP_UNAVAILABLE",
                ts_event,
                reason="UNKNOWN_STOP_MODE",
                mode=mode,
            )
            self.diagnostics["picasso_repair_stop_unavailable"] += 1
            return None

        candles = self._completed_candles(decision.symbol)
        period = int(self.config.picasso_atr_period)
        if len(candles) < period + 2:
            self.diagnostics["picasso_repair_stop_unavailable"] += 1
            return None
        atr = float(_router._atr(candles, period)[-1])
        signal = candles[-1]
        diagnostics = dict(decision.diagnostics)
        side = int(decision.side)
        middle_key = "bb_middle_long" if side > 0 else "bb_middle_short"
        try:
            middle = float(diagnostics[middle_key])
        except (KeyError, TypeError, ValueError):
            middle = math.nan
        if not math.isfinite(atr) or atr <= 0.0 or not math.isfinite(middle):
            self.diagnostics["picasso_repair_stop_unavailable"] += 1
            return None
        buffer = max(0.0, float(self.config.picasso_stop_atr_buffer)) * atr
        if mode == "signal_extreme_atr":
            anchor = min(float(signal.low), middle) if side > 0 else max(float(signal.high), middle)
        else:
            anchor = middle
        stop = anchor - buffer if side > 0 else anchor + buffer
        if not math.isfinite(stop) or stop <= 0.0:
            self.diagnostics["picasso_repair_stop_unavailable"] += 1
            return None
        return stop, {
            "stop_mode": mode,
            "source_stop": float(decision.stop_reference),
            "repaired_stop": stop,
            "repair_atr": atr,
            "repair_atr_buffer": buffer,
            "repair_signal_open": float(signal.open),
            "repair_signal_high": float(signal.high),
            "repair_signal_low": float(signal.low),
            "repair_signal_close": float(signal.close),
            "repair_bb_middle": middle,
        }

    def _submit_decision(self, decision, ts_event: int) -> None:
        repaired = self._repaired_stop(decision, ts_event)
        if repaired is None:
            return
        stop, details = repaired
        adapted = replace(decision, stop_reference=float(stop))
        before = int(self.diagnostics["entry_submissions"])
        super()._submit_decision(adapted, ts_event)
        if int(self.diagnostics["entry_submissions"]) <= before or self.current_scenario is None:
            return
        if str(self.config.picasso_stop_mode).strip().lower() != "source":
            self.diagnostics["picasso_repair_stop_submissions"] += 1
        self.current_scenario.update(
            {
                "picasso_repair_stop_mode": str(self.config.picasso_stop_mode),
                "picasso_repair_management_mode": str(self.config.picasso_management_mode),
                **details,
            }
        )

    def _source_exit_signal(self):
        mode = str(self.config.picasso_management_mode).strip().lower()
        if mode == "source":
            return super()._source_exit_signal()
        if mode in {"no_source_exit", "lifecycle", "lifecycle_progress"}:
            return False, {}
        raise ValueError(f"unsupported picasso_management_mode={mode!r}")

    def _update_path_anatomy(self) -> None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return
        entry_raw = scenario.get("actual_entry_fill", scenario.get("entry_reference"))
        try:
            entry = float(entry_raw)
        except (TypeError, ValueError):
            entry = math.nan
        side = int(scenario.get("side", 0))
        if not math.isfinite(entry) or entry <= 0.0 or side not in (-1, 1):
            return
        latest = self.bars[symbol][-1]
        favourable_price = float(latest.high) if side > 0 else float(latest.low)
        adverse_price = float(latest.low) if side > 0 else float(latest.high)
        favourable = side * (favourable_price - entry) / entry
        adverse = side * (adverse_price - entry) / entry
        current = side * (float(latest.close) - entry) / entry
        prior_mfe = float(scenario.get("picasso_repair_mfe_fraction") or 0.0)
        prior_mae = float(scenario.get("picasso_repair_mae_fraction") or 0.0)
        elapsed = max(0, self.minute_index - self.position_open_minute)
        scenario.update(
            {
                "picasso_repair_mfe_fraction": max(prior_mfe, favourable),
                "picasso_repair_mae_fraction": min(prior_mae, adverse),
                "picasso_repair_current_fraction": current,
                "picasso_repair_elapsed_minutes": elapsed,
            }
        )
        self.diagnostics["picasso_repair_path_updates"] += 1

    def _current_source_snapshot(self) -> dict[str, float | int | str] | None:
        if self.current_symbol is None or not self.bars[self.current_symbol]:
            return None
        latest_ts = int(self.bars[self.current_symbol][-1].ts_event)
        feature = _router.FeatureObservation(observed_time_ns=latest_ts, ready=True)
        decision = _router.classify_symbol(
            self.current_symbol,
            tuple(self.bars[self.current_symbol]),
            feature,
            self.route_config,
        )
        diagnostics = dict(decision.diagnostics)
        required = ("macd", "macd_signal", "bb_middle_long", "bb_middle_short")
        if not all(key in diagnostics for key in required):
            return None
        candles = self._completed_candles(self.current_symbol)
        if not candles:
            return None
        diagnostics["close"] = float(candles[-1].close)
        diagnostics["decision_state"] = str(decision.state)
        return diagnostics

    def _submit_repair_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._repair_exit_pending:
            return
        scenario = self.current_scenario
        if scenario is not None:
            scenario["picasso_repair_exit_driver"] = reason
            scenario["picasso_repair_exit_details"] = details
        counts = self.diagnostics["picasso_repair_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self._repair_exit_pending = True
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("PICASSO_REPAIR_EXIT", ts_event, reason=reason, **details)

    def _lifecycle_failure(self, snapshot: dict[str, float | int | str]) -> bool:
        scenario = self.current_scenario or {}
        side = int(scenario.get("side", 0))
        close = float(snapshot["close"])
        macd = float(snapshot["macd"])
        signal = float(snapshot["macd_signal"])
        if side > 0:
            return close < float(snapshot["bb_middle_long"]) and macd < signal
        if side < 0:
            return close > float(snapshot["bb_middle_short"]) and macd > signal
        return False

    def _progress_failure_reason(self) -> str | None:
        scenario = self.current_scenario or {}
        elapsed = int(scenario.get("picasso_repair_elapsed_minutes") or 0)
        mfe = float(scenario.get("picasso_repair_mfe_fraction") or 0.0)
        current = float(scenario.get("picasso_repair_current_fraction") or 0.0)
        leverage = max(float(self.route_config.picasso_source_effective_leverage), 1e-12)
        activation = float(self.route_config.picasso_trailing_offset) / leverage
        checkpoints = (
            (
                int(self.config.picasso_progress_checkpoint_3_minutes),
                float(self.config.picasso_progress_mfe_fraction_3),
                "PROGRESS_FAILURE_8H",
            ),
            (
                int(self.config.picasso_progress_checkpoint_2_minutes),
                float(self.config.picasso_progress_mfe_fraction_2),
                "PROGRESS_FAILURE_4H",
            ),
            (
                int(self.config.picasso_progress_checkpoint_1_minutes),
                float(self.config.picasso_progress_mfe_fraction_1),
                "PROGRESS_FAILURE_2H",
            ),
        )
        for minutes, fraction, reason in checkpoints:
            if elapsed >= minutes and mfe < activation * fraction and current <= 0.0:
                return reason
        return None

    def _manage_open_position(self, ts_event: int) -> None:
        if self._repair_exit_pending:
            return
        self._update_path_anatomy()
        scenario = self.current_scenario
        before_trailing = int(self.diagnostics.get("picasso_trailing_exits") or 0)
        before_roi = int(self.diagnostics.get("picasso_roi_exits") or 0)
        before_source = int(self.diagnostics.get("picasso_source_signal_exits") or 0)
        before_events = len(self.events)

        # Preserve the public winner engine first. Lifecycle/progress decisions
        # are considered only when trailing/ROI/source/base management did not
        # already submit an exit on this minute.
        super()._manage_open_position(ts_event)

        if scenario is not None:
            if self._trail_active and scenario.get("picasso_repair_trail_activation_minutes") is None:
                scenario["picasso_repair_trail_activation_minutes"] = max(
                    0, self.minute_index - self.position_open_minute
                )
            if int(self.diagnostics.get("picasso_trailing_exits") or 0) > before_trailing:
                scenario["picasso_repair_exit_driver"] = "PUBLIC_TRAILING_EXIT"
                return
            if int(self.diagnostics.get("picasso_roi_exits") or 0) > before_roi:
                scenario["picasso_repair_exit_driver"] = "PUBLIC_ROI_EXIT"
                return
            if int(self.diagnostics.get("picasso_source_signal_exits") or 0) > before_source:
                scenario["picasso_repair_exit_driver"] = "PUBLIC_SOURCE_SIGNAL_EXIT"
                return
            new_events = self.events[before_events:]
            if any(item.get("event_type") == "FORCED_DAYTRADE_EXIT" for item in new_events):
                scenario["picasso_repair_exit_driver"] = "FORCED_DAYTRADE_EXIT"
                return

        mode = str(self.config.picasso_management_mode).strip().lower()
        if mode not in {"lifecycle", "lifecycle_progress"} or self._repair_exit_pending:
            return
        bucket = int(self.route_config.picasso_bucket_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % bucket != bucket - 1:
            return
        snapshot = self._current_source_snapshot()
        if snapshot is None:
            return
        if self._lifecycle_failure(snapshot):
            self.diagnostics["picasso_repair_lifecycle_exits"] += 1
            self._submit_repair_exit(
                ts_event,
                "LIFECYCLE_BB_MIDDLE_MACD_FAILURE",
                close=float(snapshot["close"]),
                macd=float(snapshot["macd"]),
                macd_signal=float(snapshot["macd_signal"]),
                bb_middle_long=float(snapshot["bb_middle_long"]),
                bb_middle_short=float(snapshot["bb_middle_short"]),
            )
            return
        if mode == "lifecycle_progress":
            reason = self._progress_failure_reason()
            if reason is not None:
                self.diagnostics["picasso_repair_progress_exits"] += 1
                scenario = self.current_scenario or {}
                self._submit_repair_exit(
                    ts_event,
                    reason,
                    elapsed_minutes=int(scenario.get("picasso_repair_elapsed_minutes") or 0),
                    mfe_fraction=float(scenario.get("picasso_repair_mfe_fraction") or 0.0),
                    current_fraction=float(scenario.get("picasso_repair_current_fraction") or 0.0),
                )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
