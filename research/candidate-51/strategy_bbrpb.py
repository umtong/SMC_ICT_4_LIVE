"""NautilusTrader execution adapter for public BB_RPB_TSL entry families.

The external observations are evaluated as independent five-minute causal
families.  NautilusTrader owns fills, fees, liquidation and account accounting;
the reused execution shell enforces one global slot and exactly 3% current-NAV
planned loss.  Source entries are paired with causal swing invalidation and the
nearest available mean-reversion objective from the same completed-bar state.
"""
from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import datetime, timezone

from router import BBRPB_STATE_PREFIX, FeatureObservation, route_universe
from strategy_base import SYMBOLS
from strategy_base import Candidate35Config as _ExecutionConfig
from strategy_base import Candidate35Strategy as _ExecutionShell


class Candidate35Config(_ExecutionConfig, frozen=True):
    bbrpb_family: str = "nfi32"
    bbrpb_allow_short: bool = True
    bbrpb_bucket_minutes: int = 5
    bbrpb_structural_lookback: int = 12
    bbrpb_stop_atr_buffer: float = 0.25
    bbrpb_min_reward_r: float = 0.75
    bbrpb_min_target_fraction: float = 0.004


class Candidate35Strategy(_ExecutionShell):
    """One continuous four-symbol account for one frozen BB_RPB family."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.bars = {
            symbol: deque(self.bars[symbol], maxlen=6_000)
            for symbol in SYMBOLS
        }
        self.route_config = replace(
            self.route_config,
            bbrpb_family=str(config.bbrpb_family),
            bbrpb_allow_short=bool(config.bbrpb_allow_short),
            bbrpb_bucket_minutes=int(config.bbrpb_bucket_minutes),
            bbrpb_structural_lookback=int(config.bbrpb_structural_lookback),
            bbrpb_stop_atr_buffer=float(config.bbrpb_stop_atr_buffer),
            bbrpb_min_reward_r=float(config.bbrpb_min_reward_r),
            bbrpb_min_target_fraction=float(config.bbrpb_min_target_fraction),
        )
        self.used_episode_keys: set[tuple[str, str, int]] = set()
        self.diagnostics.update(
            {
                "external_source": "jilv220/BB_RPB_TSL:BB_RPB_TSL_BI.py",
                "bbrpb_family": str(config.bbrpb_family),
                "bbrpb_allow_short": bool(config.bbrpb_allow_short),
                "bbrpb_bucket_minutes": int(config.bbrpb_bucket_minutes),
                "source_signals_before_execution_filters": 0,
                "funding_runway_rejections": 0,
                "cooldown_rejections": 0,
                "used_episode_rejections": 0,
                "unresolved_reason_counts": {},
                "actionable_family_counts": {},
            }
        )

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
            int(self.diagnostics["max_open_positions_observed"]),
            len(open_symbols),
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
                int(self.diagnostics["max_simultaneous_entry_intents"]),
                1,
            )
            if self.minute_index - self.entry_pending_minute > 2:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="NOT_FILLED_WITHIN_TWO_COMPLETE_MINUTES",
                )
                self._clear_trade_state()
            return
        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return

        bucket_minutes = int(self.route_config.bbrpb_bucket_minutes)
        moment = datetime.fromtimestamp(
            ts_event / 1_000_000_000,
            tz=timezone.utc,
        )
        if moment.minute % bucket_minutes != bucket_minutes - 1:
            return
        required_candles = 280
        if any(
            len(self.bars[symbol]) < bucket_minutes * required_candles
            for symbol in SYMBOLS
        ):
            return

        features = {
            symbol: FeatureObservation(
                int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        self.diagnostics["quarter_hour_decisions"] += 1
        _, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol])
                for symbol in SYMBOLS
            },
            features_by_symbol=features,
            config=self.route_config,
        )
        reason_counts = self.diagnostics["unresolved_reason_counts"]
        family_counts = self.diagnostics["actionable_family_counts"]
        for decision in decisions.values():
            route_counts = self.diagnostics["route_counts"]
            route_counts[decision.state] = (
                int(route_counts.get(decision.state, 0)) + 1
            )
            if decision.actionable:
                family_counts[decision.state] = (
                    int(family_counts.get(decision.state, 0)) + 1
                )
            else:
                for reason in decision.reasons:
                    reason_counts[reason] = (
                        int(reason_counts.get(reason, 0)) + 1
                    )

        actionable = [
            decision
            for decision in decisions.values()
            if decision.actionable
        ]
        self.diagnostics["source_signals_before_execution_filters"] += len(
            actionable
        )
        unused = []
        for decision in actionable:
            key = (
                decision.symbol,
                decision.state,
                int(decision.episode_ts),
            )
            if key in self.used_episode_keys:
                self.diagnostics["used_episode_rejections"] += 1
            else:
                unused.append(decision)
        unused.sort(key=lambda item: (-item.score, item.symbol))
        winner = unused[0] if unused else None
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        if self._funding_blackout(ts_event):
            self.diagnostics["funding_runway_rejections"] += 1
            return
        if self.minute_index - self.last_entry_minute < self.config.cooldown_minutes:
            self.diagnostics["cooldown_rejections"] += 1
            return

        self.used_episode_keys.add(
            (winner.symbol, winner.state, int(winner.episode_ts))
        )
        before = int(self.diagnostics["entry_submissions"])
        self._submit_decision(winner, ts_event)
        if (
            int(self.diagnostics["entry_submissions"]) > before
            and self.current_scenario is not None
        ):
            self.current_scenario.update(
                {
                    "candidate": "candidate-51-public-bbrpb",
                    "source_family": str(self.route_config.bbrpb_family),
                    "source_direction": (
                        "reciprocal-short"
                        if int(winner.side) < 0
                        else "published-long"
                    ),
                    "risk_geometry": "causal-swing-plus-atr-buffer",
                    "objective_geometry": "completed-state-mean-reversion",
                }
            )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
