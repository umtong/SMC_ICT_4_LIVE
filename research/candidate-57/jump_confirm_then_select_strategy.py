"""Post-confirmation arbitration for simultaneous jump candidates.

The source implementation chooses one symbol at the completed four-hour event
boundary and then waits for that symbol's five-minute confirmation.  This
adapter can instead retain every already-qualified simultaneous symbol as an
unsubmitted causal candidate, evaluate the same frozen two-bar confirmation for
each, and arbitrate only among candidates which have actually transitioned.

No candidate in the pool is an exchange order.  At most one submitted entry or
open position exists across the universe, and all candidates share the original
source-event horizon.
"""
from __future__ import annotations

from dataclasses import replace
import json
import math
from typing import Any

import strategy_jump_transient as _delayed
from router import FeatureObservation, RouteDecision, route_universe
from strategy_base import SYMBOLS
from strategy_jump_transient_base import PendingJump

_SYMBOL_PRIORITY = {symbol: index for index, symbol in enumerate(SYMBOLS)}


class Candidate35Config(_delayed.Candidate35Config, frozen=True):
    jump_confirmation_pool_mode: str = "selected_then_confirm"


class Candidate35Strategy(_delayed.Candidate35Strategy):
    def __init__(self, config: Candidate35Config) -> None:
        mode = str(config.jump_confirmation_pool_mode).strip().lower()
        if mode not in {"selected_then_confirm", "confirm_then_select"}:
            raise ValueError(f"unsupported jump_confirmation_pool_mode={mode!r}")
        super().__init__(config)
        self.pending_jump_pool: dict[str, PendingJump] = {}
        self.diagnostics.update(
            {
                "jump_confirmation_pool_mode": mode,
                "jump_pool_source_events": 0,
                "jump_pool_candidates_started": 0,
                "jump_pool_candidate_confirmations": 0,
                "jump_pool_multi_confirmation_boundaries": 0,
                "jump_pool_expired_candidates": 0,
                "jump_pool_selected_entries": 0,
                "jump_pool_source_entry_changed": 0,
                "jump_pool_management_changed": 0,
                "jump_pool_candidates_are_orders": 0,
            }
        )

    def _pool_mode(self) -> str:
        return str(self.config.jump_confirmation_pool_mode).strip().lower()

    def _clear_pool(self, ts_event: int, reason: str) -> None:
        if self.pending_jump_pool:
            self._event(
                "JUMP_CONFIRMATION_POOL_CLEARED",
                ts_event,
                reason=reason,
                pending_candidates=len(self.pending_jump_pool),
                candidate_ids=sorted(self.pending_jump_pool),
            )
        self.pending_jump_pool = {}

    def _pending_from_decision(
        self, decision: RouteDecision, ts_event: int
    ) -> PendingJump | None:
        symbol = decision.symbol
        terminal = self.bars[symbol][-1]
        diagnostics = decision.diagnostics or {}
        source_entry = float(decision.entry_reference)
        if not math.isfinite(source_entry) or source_entry <= 0.0:
            return None
        candidate_id = f"{symbol}:{int(decision.episode_ts)}"
        pending = PendingJump(
            decision=decision,
            source_minute_index=self.minute_index + 1,
            source_entry=source_entry,
            terminal_high=float(terminal.high),
            terminal_low=float(terminal.low),
            extension_high=float(terminal.high),
            extension_low=float(terminal.low),
        )
        self._event(
            "JUMP_POOL_CANDIDATE_STARTED",
            ts_event,
            candidate_id=candidate_id,
            symbol=symbol,
            side=int(decision.side),
            episode_ts=int(decision.episode_ts),
            source_entry=source_entry,
            terminal_high=float(terminal.high),
            terminal_low=float(terminal.low),
            source_score=float(decision.score),
            arbitration_mode=diagnostics.get("jump_effective_arbitration_mode"),
        )
        return pending

    def _start_pool(
        self, decisions: list[RouteDecision], ts_event: int
    ) -> None:
        pool: dict[str, PendingJump] = {}
        for decision in decisions:
            candidate_id = f"{decision.symbol}:{int(decision.episode_ts)}"
            pending = self._pending_from_decision(decision, ts_event)
            if pending is not None:
                pool[candidate_id] = pending
        self.pending_jump_pool = pool
        if pool:
            self.diagnostics["jump_pool_source_events"] += 1
            self.diagnostics["jump_pool_candidates_started"] += len(pool)
            self._event(
                "JUMP_CONFIRMATION_POOL_STARTED",
                ts_event,
                candidates=len(pool),
                candidate_ids=sorted(pool),
                source_episode_ts=sorted(
                    {int(item.decision.episode_ts) for item in pool.values()}
                ),
            )

    def _confirmed_decision(
        self, pending: PendingJump, ts_event: int
    ) -> RouteDecision | None:
        elapsed = self.minute_index - pending.source_minute_index
        confirmation_minutes = int(self.route_config.jump_confirmation_minutes)
        if elapsed > confirmation_minutes:
            return None
        bucket = max(1, int(self.route_config.jump_confirmation_bucket_minutes))
        if elapsed < bucket or elapsed % bucket != 0:
            return None
        minimum_elapsed = int(self.config.jump_min_confirmation_elapsed_minutes)
        if elapsed < minimum_elapsed:
            return None

        decision = pending.decision
        symbol = decision.symbol
        latest = self.bars[symbol][-1]
        pending.extension_high = max(pending.extension_high, float(latest.high))
        pending.extension_low = min(pending.extension_low, float(latest.low))
        close = float(latest.close)
        if decision.side > 0:
            confirmed = close > pending.terminal_high
        else:
            confirmed = close < pending.terminal_low
        if not confirmed:
            return None

        diagnostics = dict(decision.diagnostics or {})
        stop_buffer_fraction = float(
            diagnostics.get(
                "stop_buffer_fraction",
                max(0.0005, self.route_config.jump_min_stop_fraction * 0.25),
            )
        )
        buffer = close * stop_buffer_fraction
        if decision.side > 0:
            stop = pending.extension_low - buffer
            planned_distance = close - stop
        else:
            stop = pending.extension_high + buffer
            planned_distance = stop - close
        if planned_distance <= 0.0 or not math.isfinite(planned_distance):
            return None
        stop_fraction = planned_distance / close
        target = close + (
            decision.side
            * float(self.route_config.jump_emergency_target_fraction)
            * close
        )
        remaining_horizon = max(
            bucket,
            int(self.config.max_hold_minutes) - max(0, elapsed),
        )
        diagnostics.update(
            {
                "source_entry_reference": pending.source_entry,
                "confirmation_entry_reference": close,
                "confirmation_delay_minutes": max(0, elapsed),
                "confirmation_terminal_high": pending.terminal_high,
                "confirmation_terminal_low": pending.terminal_low,
                "confirmation_extension_high": pending.extension_high,
                "confirmation_extension_low": pending.extension_low,
                "confirmation_stop_reference": stop,
                "confirmation_stop_fraction": stop_fraction,
                "source_clock_remaining_minutes": remaining_horizon,
                "confirmation_bucket_minutes": bucket,
                "jump_post_confirmation_pool_candidate": 1,
            }
        )
        return replace(
            decision,
            entry_reference=close,
            stop_reference=stop,
            objective_reference=target,
            decision_time_ns=ts_event,
            diagnostics=diagnostics,
        )

    def _process_pool(self, ts_event: int) -> bool:
        if not self.pending_jump_pool:
            return False
        confirmation_minutes = int(self.route_config.jump_confirmation_minutes)
        survivors: dict[str, PendingJump] = {}
        confirmed: list[RouteDecision] = []
        for candidate_id, pending in self.pending_jump_pool.items():
            symbol = pending.decision.symbol
            latest = self.bars[symbol][-1]
            pending.extension_high = max(pending.extension_high, float(latest.high))
            pending.extension_low = min(pending.extension_low, float(latest.low))
            elapsed = self.minute_index - pending.source_minute_index
            if elapsed > confirmation_minutes:
                self.diagnostics["jump_pool_expired_candidates"] += 1
                self._event(
                    "JUMP_POOL_CANDIDATE_EXPIRED",
                    ts_event,
                    candidate_id=candidate_id,
                    symbol=symbol,
                    episode_ts=int(pending.decision.episode_ts),
                    elapsed_minutes=elapsed,
                )
                continue
            decision = self._confirmed_decision(pending, ts_event)
            if decision is not None:
                confirmed.append(decision)
                self.diagnostics["jump_pool_candidate_confirmations"] += 1
            else:
                survivors[candidate_id] = pending

        if not confirmed:
            self.pending_jump_pool = survivors
            return bool(survivors)

        if len(confirmed) > 1:
            self.diagnostics["jump_pool_multi_confirmation_boundaries"] += 1
        confirmed.sort(
            key=lambda item: (
                -float(item.score),
                _SYMBOL_PRIORITY.get(item.symbol, 99),
                int(item.episode_ts),
            )
        )
        selected = confirmed[0]
        diagnostics = dict(selected.diagnostics or {})
        diagnostics.update(
            {
                "jump_post_confirmation_pool_selected": 1,
                "jump_post_confirmation_pool_size": len(
                    self.pending_jump_pool
                ),
                "jump_same_minute_confirmed_candidates": len(confirmed),
                "jump_same_minute_confirmed_set_json": json.dumps(
                    [
                        {
                            "symbol": item.symbol,
                            "side": int(item.side),
                            "score": float(item.score),
                            "episode_ts": int(item.episode_ts),
                        }
                        for item in confirmed
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        selected = replace(selected, diagnostics=diagnostics)
        self.pending_jump_pool = {}
        scenario_key = (selected.symbol, selected.state, selected.episode_ts)
        if scenario_key in self.used_episode_keys:
            return False
        self.used_episode_keys.add(scenario_key)
        self.diagnostics["jump_pool_selected_entries"] += 1
        self._event(
            "JUMP_POST_CONFIRMATION_POOL_SELECTED",
            ts_event,
            symbol=selected.symbol,
            side=int(selected.side),
            episode_ts=int(selected.episode_ts),
            confirmed_candidates=len(confirmed),
            entry_reference=float(selected.entry_reference),
            stop_reference=float(selected.stop_reference),
        )
        self._submit_decision(selected, ts_event)
        return True

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        if self._pool_mode() == "selected_then_confirm":
            super()._on_complete_universe_minute(ts_event)
            return

        self._update_pending_entries()
        if self.current_symbol is not None:
            self._manage_open_position(ts_event)
            if self.current_symbol is not None:
                return
        if self._has_account_level_pending_entry():
            return

        if self.pending_jump_pool:
            active_or_submitted = self._process_pool(ts_event)
            if self.current_symbol is not None or self._has_account_level_pending_entry():
                return
            if active_or_submitted or self.pending_jump_pool:
                return

        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return
        if any(not self.bars[symbol] for symbol in SYMBOLS):
            return
        observations = {
            symbol: FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol]) for symbol in SYMBOLS
            },
            features_by_symbol=observations,
            config=self.route_config,
        )
        self._record_route_decisions(decisions)
        actionable: list[RouteDecision] = []
        for decision in decisions.values():
            if not decision.actionable:
                continue
            scenario_key = (
                decision.symbol,
                decision.state,
                decision.episode_ts,
            )
            if scenario_key not in self.used_episode_keys:
                actionable.append(decision)
        if not actionable:
            return
        actionable.sort(
            key=lambda item: (
                -float(item.score),
                _SYMBOL_PRIORITY.get(item.symbol, 99),
                int(item.episode_ts),
            )
        )
        self._start_pool(actionable, ts_event)

    def _end_flatten(self, ts_event: int) -> None:
        self._clear_pool(ts_event, "evaluation_end")
        super()._end_flatten(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
