"""Causal-episode terminal-state repair for the public Slope system.

The public Slope entry, the 2x source-relative runway geometry, structural risk,
public trailing/ROI and the selected source exit remain unchanged.  This module
changes only what happens after an observed thesis failure:

* ``repair_exit`` marks the current contiguous source condition terminal when a
  source-progress/condition repair exits an underwater trade;
* ``any_loss`` marks it terminal after any valid losing close (including the
  structural stop or MA-thesis exit);
* a terminal condition is not allowed to re-enter while its causal run-start
  timestamp remains the same;
* if the highest-scoring symbol is terminal, the next eligible non-terminal
  symbol is considered, preserving the one-global-slot opportunity set.

No future result is used: terminal state is created only after a realized event
or an already-causal repair decision.  A fresh condition receives a new run
start timestamp and is eligible again automatically.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any

import router as _router
import strategy_slope_repair as _base

SYMBOLS = _base._base.SYMBOLS
_SYMBOL_PRIORITY = {"BTCUSDT": 0, "ETHUSDT": 1, "SOLUSDT": 2, "XRPUSDT": 3}
_PNL_PATTERN = re.compile(r"[-+]?\d+(?:[,_]\d{3})*(?:\.\d+)?")


class Candidate35Config(_base.Candidate35Config, frozen=True):
    # none | repair_exit | any_loss
    slope_terminal_mode: str = "none"


class Candidate35Strategy(_base.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        mode = str(config.slope_terminal_mode).strip().lower()
        if mode not in {"none", "repair_exit", "any_loss"}:
            raise ValueError(f"unsupported slope_terminal_mode={mode!r}")
        self._terminal_mode = mode
        self._terminal_conditions: dict[tuple[str, int, int], dict[str, Any]] = {}
        self.diagnostics.update(
            {
                "slope_terminal_mode": mode,
                "slope_terminal_blocks_created": 0,
                "slope_terminal_duplicate_blocks": 0,
                "slope_terminal_decisions_blocked": 0,
                "slope_terminal_hours_without_alternative": 0,
                "slope_terminal_alternative_selections": 0,
                "slope_terminal_block_reason_counts": {},
                "slope_terminal_blocked_keys": [],
            }
        )

    @staticmethod
    def _decision_key(decision: Any) -> tuple[str, int, int] | None:
        diagnostics = dict(getattr(decision, "diagnostics", {}) or {})
        start = diagnostics.get("condition_run_start_ts")
        if start is None:
            start = getattr(decision, "episode_ts", 0)
        try:
            key = (str(decision.symbol), int(decision.side), int(start))
        except (TypeError, ValueError, AttributeError):
            return None
        return key if key[1] in (-1, 1) and key[2] > 0 else None

    @staticmethod
    def _scenario_key(scenario: dict[str, Any] | None) -> tuple[str, int, int] | None:
        if not scenario:
            return None
        diagnostics = scenario.get("diagnostics")
        diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
        start = diagnostics.get("condition_run_start_ts")
        if start is None:
            start = scenario.get("episode_ts")
        try:
            key = (str(scenario.get("symbol") or ""), int(scenario.get("side") or 0), int(start or 0))
        except (TypeError, ValueError):
            return None
        return key if key[0] and key[1] in (-1, 1) and key[2] > 0 else None

    @staticmethod
    def _pnl(value: object) -> float:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            return number if math.isfinite(number) else math.nan
        match = _PNL_PATTERN.search(str(value).replace("_", ""))
        if match is None:
            return math.nan
        try:
            return float(match.group().replace(",", ""))
        except ValueError:
            return math.nan

    def _block_condition(
        self,
        scenario: dict[str, Any] | None,
        *,
        reason: str,
        ts_event: int,
    ) -> None:
        key = self._scenario_key(scenario)
        if key is None:
            return
        if key in self._terminal_conditions:
            self.diagnostics["slope_terminal_duplicate_blocks"] += 1
            return
        metadata = {
            "symbol": key[0],
            "side": key[1],
            "condition_run_start_ts": key[2],
            "blocked_ts_event": int(ts_event),
            "reason": str(reason),
            "scenario_id": None if not scenario else scenario.get("scenario_id"),
        }
        self._terminal_conditions[key] = metadata
        self.diagnostics["slope_terminal_blocks_created"] += 1
        reasons = self.diagnostics["slope_terminal_block_reason_counts"]
        reasons[str(reason)] = int(reasons.get(str(reason), 0)) + 1
        self.diagnostics["slope_terminal_blocked_keys"] = list(
            self._terminal_conditions.values()
        )
        if scenario is not None:
            scenario["slope_terminal_condition_created"] = metadata
        self._event("SLOPE_TERMINAL_CONDITION", int(ts_event), **metadata)

    def _submit_repair_exit(self, ts_event: int, reason: str, **details: Any) -> None:
        if self._terminal_mode == "repair_exit":
            self._block_condition(
                self.current_scenario,
                reason=f"REPAIR_EXIT:{reason}",
                ts_event=ts_event,
            )
        super()._submit_repair_exit(ts_event, reason, **details)

    def _after_position_closed(self, event: Any, record: dict[str, Any]) -> None:
        if (
            self._terminal_mode == "any_loss"
            and record.get("actual_fill_risk_valid") is not False
        ):
            pnl = self._pnl(record.get("realized_pnl"))
            if math.isfinite(pnl) and pnl < 0.0:
                driver = str(
                    record.get("slope_exit_driver")
                    or record.get("slope_terminal_exit_driver")
                    or "NEGATIVE_CLOSE"
                )
                self._block_condition(
                    record,
                    reason=f"VALID_LOSS:{driver}",
                    ts_event=int(getattr(event, "ts_event", record.get("closed_ts_event") or self._latest_ts())),
                )
        super()._after_position_closed(event, record)

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        # This is the public strategy's account lifecycle with only the final
        # cross-asset selection replaced by terminal-aware arbitration.
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)
        open_symbols = [
            symbol for symbol in SYMBOLS
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
                self._event("ENTRY_EXPIRED", ts_event, reason="SLOPE_PARENT_NOT_FILLED")
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
        actionable: list[Any] = []
        blocked_actionable = 0
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if decision.actionable:
                self.diagnostics["slope_source_conditions"] += 1
                key = self._decision_key(decision)
                if key is not None and key in self._terminal_conditions:
                    blocked_actionable += 1
                    self.diagnostics["slope_terminal_decisions_blocked"] += 1
                else:
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
        if not actionable:
            self.diagnostics["unresolved_episodes"] += 1
            if blocked_actionable:
                self.diagnostics["slope_terminal_hours_without_alternative"] += 1
            return
        winner = actionable[0]
        original_actionable = [
            decision for decision in decisions.values() if decision.actionable
        ]
        original_actionable.sort(
            key=lambda decision: (
                -float(decision.score),
                _SYMBOL_PRIORITY.get(decision.symbol, 99),
                -int(decision.side),
            )
        )
        if original_actionable and original_actionable[0].symbol != winner.symbol:
            self.diagnostics["slope_terminal_alternative_selections"] += 1
        self.diagnostics["slope_entry_candidates"] += 1
        self._submit_decision(winner, ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
