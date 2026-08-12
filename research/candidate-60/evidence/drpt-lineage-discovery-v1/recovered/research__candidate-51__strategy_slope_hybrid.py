"""Episode-aware risk and lifecycle synthesis for Slope-is-Dope.

The public hourly entry and trailing/ROI winner engine are retained.  This file
separates four mechanisms which were conflated in the source:

* contiguous condition generation versus re-entry after a realised profit;
* wide source risk versus structural risk geometry;
* moderate trend continuation versus weak or exhausted trend state;
* public lifecycle versus causal no-progress or condition-loss exits.

Every decision is made from information available at that timestamp.  Rejected
and displaced actionable candidates are retained in diagnostics for no-trade
analysis.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import math
import re
from typing import Any

import router as _router
import strategy_slope_dope as _base

SYMBOLS = _base.SYMBOLS
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}


class Candidate35Config(_base.Candidate35Config, frozen=True):
    # condition | profit_capture
    slope_hybrid_reentry_policy: str = "condition"

    # source_all | structural_all | source_fresh_structural_profit | quality_hybrid
    slope_hybrid_stop_policy: str = "source_all"
    # source | skip, used only when quality_hybrid rejects structural geometry
    slope_hybrid_nonquality_action: str = "source"

    # source | progress_only | condition_loss | progress_thesis
    slope_hybrid_management: str = "source"
    slope_hybrid_progress_checkpoint_1_minutes: int = 360
    slope_hybrid_progress_checkpoint_2_minutes: int = 960
    slope_hybrid_progress_activation_fraction_1: float = 0.25
    slope_hybrid_progress_activation_fraction_2: float = 1.00

    # Broad mechanistic band: weak trends use source geometry or no trade,
    # while highly separated/exhausted trends are not amplified.
    slope_hybrid_adx_margin_min: float = 5.0
    slope_hybrid_ma_separation_min: float = 0.005
    slope_hybrid_ma_separation_max: float = 0.070
    slope_hybrid_slope_strength_min: float = 0.00075
    slope_hybrid_slope_strength_max: float = 0.0080
    slope_hybrid_long_rsi_max: float = 90.0
    slope_hybrid_short_rsi_min: float = 20.0


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self._last_closed_by_episode: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._pending_entry_context: dict[str, Any] | None = None
        self.diagnostics.update(
            {
                "slope_hybrid_reentry_policy": str(config.slope_hybrid_reentry_policy),
                "slope_hybrid_stop_policy": str(config.slope_hybrid_stop_policy),
                "slope_hybrid_nonquality_action": str(config.slope_hybrid_nonquality_action),
                "slope_hybrid_management": str(config.slope_hybrid_management),
                "slope_hybrid_profit_reentry_allowed": 0,
                "slope_hybrid_profit_reentry_rejected": 0,
                "slope_hybrid_quality_structural": 0,
                "slope_hybrid_quality_source_fallback": 0,
                "slope_hybrid_quality_skipped": 0,
                "slope_hybrid_source_stop_submissions": 0,
                "slope_hybrid_structural_stop_submissions": 0,
                "slope_hybrid_condition_loss_exits": 0,
                "slope_hybrid_no_progress_exits": 0,
                "slope_hybrid_exit_counts": {},
                "slope_hybrid_rejections": [],
                "slope_hybrid_decision_trace": [],
            }
        )

    @staticmethod
    def _pnl(value: Any) -> float:
        match = re.search(r"[-+]?\d+(?:[,_]\d{3})*(?:\.\d+)?", str(value).replace("_", ""))
        return float(match.group().replace(",", "")) if match else 0.0

    @staticmethod
    def _episode_key_from_decision(decision: Any) -> tuple[str, int, int]:
        diagnostics = dict(decision.diagnostics)
        return (
            str(decision.symbol),
            int(decision.side),
            int(diagnostics.get("condition_run_start_ts") or 0),
        )

    def _after_position_closed(self, event: Any, record: dict[str, Any]) -> None:
        diagnostics = dict(record.get("diagnostics") or {})
        key = (
            str(record.get("symbol") or ""),
            int(record.get("side") or 0),
            int(record.get("condition_run_start_ts") or diagnostics.get("condition_run_start_ts") or 0),
        )
        snapshot = {
            "symbol": key[0],
            "side": key[1],
            "condition_run_start_ts": key[2],
            "condition_run_hours": int(
                record.get("condition_run_hours") or diagnostics.get("condition_run_hours") or 0
            ),
            "exit_driver": record.get("slope_exit_driver"),
            "pnl": self._pnl(record.get("realized_pnl")),
            "closed_ts_event": int(record.get("closed_ts_event") or 0),
            "actual_stop_mode": record.get("slope_actual_stop_mode"),
        }
        super()._after_position_closed(event, record)
        if key[0] and key[1] in (-1, 1) and key[2] > 0:
            self._last_closed_by_episode[key] = snapshot

    def _quality_state(self, decision: Any) -> tuple[bool, tuple[str, ...], dict[str, float]]:
        diagnostics = dict(decision.diagnostics)
        side = int(decision.side)
        adx = float(diagnostics.get("adx") or 0.0)
        threshold = (
            float(self.config.slope_long_adx_min)
            if side > 0
            else float(self.config.slope_short_adx_min)
        )
        adx_margin = adx - threshold
        separation = float(diagnostics.get("ma_separation_fraction") or 0.0)
        strength = float(diagnostics.get("slope_strength_fraction") or 0.0)
        rsi = float(diagnostics.get("rsi") or 0.0)
        reasons: list[str] = []
        if adx_margin < float(self.config.slope_hybrid_adx_margin_min):
            reasons.append("ADX_MARGIN_WEAK")
        if separation < float(self.config.slope_hybrid_ma_separation_min):
            reasons.append("MA_SEPARATION_WEAK")
        if separation > float(self.config.slope_hybrid_ma_separation_max):
            reasons.append("MA_SEPARATION_EXHAUSTED")
        if strength < float(self.config.slope_hybrid_slope_strength_min):
            reasons.append("SLOPE_STRENGTH_WEAK")
        if strength > float(self.config.slope_hybrid_slope_strength_max):
            reasons.append("SLOPE_STRENGTH_EXHAUSTED")
        if side > 0 and rsi > float(self.config.slope_hybrid_long_rsi_max):
            reasons.append("RSI_LONG_EXHAUSTED")
        if side < 0 and rsi < float(self.config.slope_hybrid_short_rsi_min):
            reasons.append("RSI_SHORT_EXHAUSTED")
        details = {
            "adx_margin": adx_margin,
            "ma_separation_fraction": separation,
            "slope_strength_fraction": strength,
            "rsi": rsi,
        }
        return not reasons, tuple(reasons), details

    def _structural_stop(self, decision: Any) -> tuple[float, dict[str, float | str]] | None:
        candles = self._hourly_candles(decision.symbol)
        period = int(self.config.slope_stop_atr_period)
        if len(candles) < period + 2:
            return None
        atr = float(_base._ta._atr(candles, period)[-1])
        diagnostics = dict(decision.diagnostics)
        try:
            slow = float(diagnostics["slow_sma"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(atr) or atr <= 0.0 or not math.isfinite(slow):
            return None
        signal = candles[-1]
        buffer = max(0.0, float(self.config.slope_stop_atr_buffer)) * atr
        source_stop = float(decision.stop_reference)
        if int(decision.side) > 0:
            structural = min(float(signal.low), slow) - buffer
            stop = max(source_stop, structural)
        else:
            structural = max(float(signal.high), slow) + buffer
            stop = min(source_stop, structural)
        if not math.isfinite(stop) or stop <= 0.0:
            return None
        return stop, {
            "slope_actual_stop_mode": "signal_slow_atr",
            "slope_source_stop": source_stop,
            "slope_structural_stop_raw": structural,
            "slope_repaired_stop": stop,
            "slope_stop_atr": atr,
            "slope_stop_buffer": buffer,
            "slope_signal_high": float(signal.high),
            "slope_signal_low": float(signal.low),
            "slope_slow_sma_anchor": slow,
        }

    def _entry_policy(self, decision: Any) -> dict[str, Any]:
        diagnostics = dict(decision.diagnostics)
        side = int(decision.side)
        prior_active = bool(
            diagnostics.get("previous_long_condition")
            if side > 0
            else diagnostics.get("previous_short_condition")
        )
        run_start = int(diagnostics.get("condition_run_start_ts") or 0)
        run_hours = int(diagnostics.get("condition_run_hours") or 0)
        fresh = not prior_active or run_hours <= 1
        key = self._episode_key_from_decision(decision)
        prior = self._last_closed_by_episode.get(key)
        profitable_capture = bool(
            prior
            and float(prior.get("pnl") or 0.0) > 0.0
            and prior.get("exit_driver") in {"PUBLIC_TRAILING_EXIT", "PUBLIC_ROI_EXIT"}
        )
        reentry_policy = str(self.config.slope_hybrid_reentry_policy).strip().lower()
        if reentry_policy not in {"condition", "profit_capture"}:
            raise ValueError(f"unsupported slope_hybrid_reentry_policy={reentry_policy!r}")
        if reentry_policy == "profit_capture" and not fresh and not profitable_capture:
            return {
                "allow": False,
                "reason": "PROFIT_CAPTURE_REENTRY_NOT_PROVEN",
                "fresh": fresh,
                "prior_active": prior_active,
                "run_start": run_start,
                "run_hours": run_hours,
                "profitable_capture": profitable_capture,
                "prior": prior,
            }

        policy = str(self.config.slope_hybrid_stop_policy).strip().lower()
        quality, quality_reasons, quality_details = self._quality_state(decision)
        stop_mode = "source"
        if policy == "source_all":
            stop_mode = "source"
        elif policy == "structural_all":
            stop_mode = "structural"
        elif policy == "source_fresh_structural_profit":
            stop_mode = "source" if fresh else "structural"
        elif policy == "quality_hybrid":
            if fresh:
                stop_mode = "source"
            elif quality:
                stop_mode = "structural"
            else:
                action = str(self.config.slope_hybrid_nonquality_action).strip().lower()
                if action == "source":
                    stop_mode = "source"
                elif action == "skip":
                    return {
                        "allow": False,
                        "reason": "STRUCTURAL_QUALITY_NOT_PROVEN",
                        "fresh": fresh,
                        "prior_active": prior_active,
                        "run_start": run_start,
                        "run_hours": run_hours,
                        "profitable_capture": profitable_capture,
                        "prior": prior,
                        "quality": quality,
                        "quality_reasons": quality_reasons,
                        **quality_details,
                    }
                else:
                    raise ValueError(
                        f"unsupported slope_hybrid_nonquality_action={action!r}"
                    )
        else:
            raise ValueError(f"unsupported slope_hybrid_stop_policy={policy!r}")

        if stop_mode == "source":
            stop = float(decision.stop_reference)
            stop_details: dict[str, float | str] = {
                "slope_actual_stop_mode": "source",
                "slope_source_stop": stop,
                "slope_repaired_stop": stop,
            }
        else:
            packed = self._structural_stop(decision)
            if packed is None:
                return {
                    "allow": False,
                    "reason": "STRUCTURAL_STOP_UNAVAILABLE",
                    "fresh": fresh,
                    "prior_active": prior_active,
                    "run_start": run_start,
                    "run_hours": run_hours,
                    "profitable_capture": profitable_capture,
                    "prior": prior,
                    "quality": quality,
                    "quality_reasons": quality_reasons,
                    **quality_details,
                }
            stop, stop_details = packed

        return {
            "allow": True,
            "reason": "ENTRY_ALLOWED",
            "fresh": fresh,
            "prior_active": prior_active,
            "run_start": run_start,
            "run_hours": run_hours,
            "profitable_capture": profitable_capture,
            "prior": prior,
            "quality": quality,
            "quality_reasons": quality_reasons,
            "stop_mode": stop_mode,
            "stop": stop,
            "stop_details": stop_details,
            **quality_details,
        }

    @staticmethod
    def _compact_prior(prior: Any) -> dict[str, Any] | None:
        if not isinstance(prior, dict):
            return None
        return {
            "pnl": float(prior.get("pnl") or 0.0),
            "exit_driver": prior.get("exit_driver"),
            "closed_ts_event": int(prior.get("closed_ts_event") or 0),
            "actual_stop_mode": prior.get("actual_stop_mode"),
        }

    def _trace_candidate(
        self,
        ts_event: int,
        decision: Any,
        context: dict[str, Any],
        selected: bool,
    ) -> dict[str, Any]:
        diagnostics = dict(decision.diagnostics)
        return {
            "ts_event": int(ts_event),
            "symbol": str(decision.symbol),
            "side": int(decision.side),
            "score": float(decision.score),
            "signal_ts": int(decision.episode_ts),
            "condition_run_start_ts": int(context.get("run_start") or 0),
            "condition_run_hours": int(context.get("run_hours") or 0),
            "fresh": bool(context.get("fresh")),
            "profitable_capture": bool(context.get("profitable_capture")),
            "allow": bool(context.get("allow")),
            "reason": str(context.get("reason")),
            "selected": bool(selected),
            "stop_mode": context.get("stop_mode"),
            "quality": bool(context.get("quality", False)),
            "quality_reasons": list(context.get("quality_reasons") or ()),
            "prior": self._compact_prior(context.get("prior")),
            "adx": float(diagnostics.get("adx") or 0.0),
            "rsi": float(diagnostics.get("rsi") or 0.0),
            "ma_separation_fraction": float(
                diagnostics.get("ma_separation_fraction") or 0.0
            ),
            "slope_strength_fraction": float(
                diagnostics.get("slope_strength_fraction") or 0.0
            ),
        }

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        self.diagnostics["max_open_positions_observed"] = max(
            int(self.diagnostics["max_open_positions_observed"]), len(open_symbols)
        )
        if len(open_symbols) > 1:
            self.diagnostics["global_position_violations"] += 1
            for symbol in open_symbols:
                self.cancel_all_orders(self.instrument_ids[symbol])
                self.close_all_positions(self.instrument_ids[symbol])
            return
        if open_symbols:
            self.current_symbol = open_symbols[0]
            self._manage_open_position(ts_event)
            return
        if self.entry_pending:
            self.diagnostics["max_simultaneous_entry_intents"] = max(
                int(self.diagnostics["max_simultaneous_entry_intents"]), 1
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="SLOPE_HYBRID_PARENT_NOT_FILLED",
                )
                self._clear_trade_state()
            return
        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return
        if self._funding_blackout(ts_event):
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            return
        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute != 59:
            return

        self.diagnostics["slope_hourly_decisions"] += 1
        features = {
            symbol: _router.FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        _, decisions = _router.route_universe(
            {symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS},
            features,
            self.route_config,
        )
        actionable = []
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                self.diagnostics["slope_source_conditions"] += 1
                actionable.append(decision)
            else:
                reason = decision.reasons[0] if decision.reasons else "UNKNOWN"
                reasons = self.diagnostics["unresolved_reason_counts"]
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        actionable.sort(
            key=lambda decision: (
                -float(decision.score),
                _SYMBOL_PRIORITY.get(decision.symbol, 99),
                -int(decision.side),
            )
        )

        chosen = None
        chosen_context = None
        candidate_trace = []
        for decision in actionable:
            context = self._entry_policy(decision)
            selected = chosen is None and bool(context.get("allow"))
            candidate_trace.append(
                self._trace_candidate(ts_event, decision, context, selected)
            )
            if chosen is None and bool(context.get("allow")):
                chosen = decision
                chosen_context = context
            elif not bool(context.get("allow")):
                rejection = self._trace_candidate(
                    ts_event, decision, context, False
                )
                self.diagnostics["slope_hybrid_rejections"].append(rejection)
                if context.get("reason") == "PROFIT_CAPTURE_REENTRY_NOT_PROVEN":
                    self.diagnostics["slope_hybrid_profit_reentry_rejected"] += 1
                if context.get("reason") == "STRUCTURAL_QUALITY_NOT_PROVEN":
                    self.diagnostics["slope_hybrid_quality_skipped"] += 1

        if candidate_trace:
            self.diagnostics["slope_hybrid_decision_trace"].append(
                {
                    "ts_event": int(ts_event),
                    "selected_symbol": chosen.symbol if chosen is not None else None,
                    "candidates": candidate_trace,
                }
            )
        if chosen is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self.diagnostics["slope_entry_candidates"] += 1
        self._pending_entry_context = chosen_context
        self._submit_decision(chosen, ts_event)
        self._pending_entry_context = None

    def _submit_decision(self, decision: Any, ts_event: int) -> None:
        context = self._pending_entry_context or self._entry_policy(decision)
        if not bool(context.get("allow")):
            self._event(
                "SLOPE_HYBRID_ENTRY_REJECTED",
                ts_event,
                symbol=decision.symbol,
                side=int(decision.side),
                reason=context.get("reason"),
            )
            return
        if not bool(context.get("fresh")) and bool(context.get("profitable_capture")):
            self.diagnostics["slope_hybrid_profit_reentry_allowed"] += 1
        stop_mode = str(context.get("stop_mode"))
        if stop_mode == "structural":
            self.diagnostics["slope_hybrid_structural_stop_submissions"] += 1
            if bool(context.get("quality")):
                self.diagnostics["slope_hybrid_quality_structural"] += 1
        else:
            self.diagnostics["slope_hybrid_source_stop_submissions"] += 1
            if (
                str(self.config.slope_hybrid_stop_policy).strip().lower()
                == "quality_hybrid"
                and not bool(context.get("fresh"))
                and not bool(context.get("quality"))
            ):
                self.diagnostics["slope_hybrid_quality_source_fallback"] += 1

        before = int(self.diagnostics["entry_submissions"])
        _base._base.Candidate35Strategy._submit_decision(
            self,
            replace(decision, stop_reference=float(context["stop"])),
            ts_event,
        )
        if int(self.diagnostics["entry_submissions"]) <= before or self.current_scenario is None:
            return
        diagnostics = dict(decision.diagnostics)
        self.current_scenario.update(
            {
                "condition_run_start_ts": int(context.get("run_start") or 0),
                "condition_run_hours": int(context.get("run_hours") or 0),
                "slope_hybrid_reentry_policy": str(
                    self.config.slope_hybrid_reentry_policy
                ),
                "slope_hybrid_stop_policy": str(self.config.slope_hybrid_stop_policy),
                "slope_hybrid_management": str(self.config.slope_hybrid_management),
                "slope_hybrid_fresh": bool(context.get("fresh")),
                "slope_hybrid_profitable_capture": bool(
                    context.get("profitable_capture")
                ),
                "slope_hybrid_quality": bool(context.get("quality")),
                "slope_hybrid_quality_reasons": list(
                    context.get("quality_reasons") or ()
                ),
                "slope_hybrid_prior_capture": self._compact_prior(
                    context.get("prior")
                ),
                "slope_hybrid_adx_margin": float(context.get("adx_margin") or 0.0),
                "slope_hybrid_ma_separation_fraction": float(
                    context.get("ma_separation_fraction") or 0.0
                ),
                "slope_hybrid_slope_strength_fraction": float(
                    context.get("slope_strength_fraction") or 0.0
                ),
                "slope_hybrid_rsi": float(context.get("rsi") or 0.0),
                **dict(context.get("stop_details") or {}),
            }
        )
        self.current_scenario["diagnostics"] = {
            **diagnostics,
            "condition_run_start_ts": int(context.get("run_start") or 0),
            "condition_run_hours": int(context.get("run_hours") or 0),
        }

    def _submit_hybrid_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self.current_symbol is None or self._exit_pending:
            return
        if self.current_scenario is not None:
            self.current_scenario["slope_exit_driver"] = reason
            self.current_scenario["slope_hybrid_exit_details"] = details
        counts = self.diagnostics["slope_hybrid_exit_counts"]
        counts[reason] = int(counts.get(reason, 0)) + 1
        self._exit_pending = True
        instrument_id = self.instrument_ids[self.current_symbol]
        self.cancel_all_orders(instrument_id)
        self.close_all_positions(instrument_id)
        self._event("SLOPE_HYBRID_EXIT", ts_event, reason=reason, **details)

    def _repair_state(
        self,
    ) -> tuple[dict[str, float | int | str], float, float, int] | None:
        scenario = self.current_scenario
        symbol = self.current_symbol
        if scenario is None or symbol is None or not self.bars[symbol]:
            return None
        state = _router.inspect_condition(tuple(self.bars[symbol]), self.route_config)
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
        super()._manage_open_position(ts_event)
        if self._exit_pending:
            return
        management = str(self.config.slope_hybrid_management).strip().lower()
        if management == "source":
            return
        if management not in {
            "progress_only",
            "condition_loss",
            "progress_thesis",
        }:
            raise ValueError(f"unsupported slope_hybrid_management={management!r}")
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
        if (
            management in {"condition_loss", "progress_thesis"}
            and not condition_active
            and current <= 0.0
        ):
            self.diagnostics["slope_hybrid_condition_loss_exits"] += 1
            self._submit_hybrid_exit(
                ts_event,
                "SLOPE_SOURCE_CONDITION_LOST_UNDERWATER",
                held_minutes=held,
                mfe_fraction=mfe,
                current_return_fraction=current,
                adx=float(state.get("adx") or 0.0),
                rsi=float(state.get("rsi") or 0.0),
            )
            return
        if management not in {"progress_only", "progress_thesis"}:
            return
        if scenario.get("slope_trail_activation_minutes") is not None:
            return

        leverage = max(float(self.config.slope_source_leverage), 1e-12)
        activation = (
            float(self.config.slope_trailing_offset_profit_ratio) / leverage
        )
        checkpoints = (
            (
                int(self.config.slope_hybrid_progress_checkpoint_2_minutes),
                float(self.config.slope_hybrid_progress_activation_fraction_2),
                "SLOPE_NO_PROGRESS_LATE",
            ),
            (
                int(self.config.slope_hybrid_progress_checkpoint_1_minutes),
                float(self.config.slope_hybrid_progress_activation_fraction_1),
                "SLOPE_NO_PROGRESS_EARLY",
            ),
        )
        for minutes, fraction, reason in checkpoints:
            if (
                minutes > 0
                and held >= minutes
                and mfe < activation * fraction
                and current <= 0.0
            ):
                self.diagnostics["slope_hybrid_no_progress_exits"] += 1
                self._submit_hybrid_exit(
                    ts_event,
                    reason,
                    held_minutes=held,
                    mfe_fraction=mfe,
                    required_mfe_fraction=activation * fraction,
                    current_return_fraction=current,
                    condition_active=condition_active,
                    actual_stop_mode=scenario.get("slope_actual_stop_mode"),
                    adx=float(state.get("adx") or 0.0),
                    rsi=float(state.get("rsi") or 0.0),
                )
                return


__all__ = ["Candidate35Config", "Candidate35Strategy"]
