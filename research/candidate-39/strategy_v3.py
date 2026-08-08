"""Candidate 39 V3 Nautilus adapter for trapped-inventory release.

The V2 router/execution path is preserved. V3 adds a persistent setup state
which is observed after a failed leveraged attack and can only enter after a
later, separately completed opposite release auction. NautilusTrader and the
Candidate 35 shell continue to own orders, fills, fees, margin, positions and
continuous account NAV.
"""
from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent

import router_v3 as _router

# Load the complete V2 strategy against the V3-compatible router contract.
sys.modules["router"] = _router
_spec = importlib.util.spec_from_file_location(
    "_candidate39_v2_strategy_for_v3",
    HERE / "strategy.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load Candidate 39 V2 strategy from {HERE / 'strategy.py'}")
_v2 = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _v2
_spec.loader.exec_module(_v2)

Candidate39Config = _v2.Candidate39Config
Candidate35Config = Candidate39Config


class Candidate39V3Strategy(_v2.Candidate39Strategy):
    """Four-asset V2 router plus one independent trapped-release family."""

    def __init__(self, config: Candidate39Config) -> None:
        super().__init__(config)
        self.trap_config = _router.TrappedBuildConfig()
        self.trapped_setups: dict[str, _router.TrappedBuildSetup] = {}
        self.diagnostics.update(
            {
                "trapped_build_setups_detected": 0,
                "trapped_build_same_episode_suppressed": 0,
                "trapped_build_setups_expired": 0,
                "trapped_build_setups_invalidated": 0,
                "trapped_build_release_pending_checks": 0,
                "trapped_build_releases_confirmed": 0,
                "trapped_build_release_geometry_rejections": 0,
                "trapped_build_release_global_ambiguity": 0,
                "trapped_build_release_blocked_no_chase": 0,
                "trapped_build_entries_submitted": 0,
                "max_simultaneous_trapped_setups": 0,
                "v3_identity_rebindings": 0,
            }
        )

    def _current_features(
        self,
        ts_event: int,
    ) -> dict[str, _router.FeatureObservation]:
        return {
            symbol: self.features[symbol].observation(
                ts_event,
                self.config.feature_max_age_seconds,
            )
            for symbol in _v2._base.SYMBOLS
        }

    def _bind_v3_identity(self, decision: _router.RouteDecision) -> None:
        if not self.current_scenario:
            return
        self.current_scenario["candidate"] = (
            "candidate-39-causal-auction-state-router-v3"
        )
        self.current_scenario["scenario_id"] = (
            f"c39v3-{self.diagnostics['entry_submissions']:07d}"
        )
        self.current_scenario["state_machine"] = (
            "FAILED_ATTACK_SETUP_TO_SEPARATE_RELEASE_AUCTION"
            if decision.state == "TRAPPED_BUILD_RELEASE"
            else "V2_CAUSAL_AUCTION_FAMILY_PRESERVED"
        )
        self.current_scenario["candidate_version"] = 3
        self.diagnostics["v3_identity_rebindings"] += 1

    def _submit_decision(
        self,
        decision: _router.RouteDecision,
        ts_event: int,
    ) -> None:
        before = bool(self.entry_pending)
        super()._submit_decision(decision, ts_event)
        if not before and self.entry_pending:
            self._bind_v3_identity(decision)
            if decision.state == "TRAPPED_BUILD_RELEASE":
                self.diagnostics["trapped_build_entries_submitted"] += 1
            # Any submitted trade consumes the common causal opportunity set.
            self.trapped_setups.clear()
            self._event(
                "V3_DECISION_BOUND",
                ts_event,
                candidate="candidate-39-causal-auction-state-router-v3",
                state=decision.state,
                symbol=decision.symbol,
                side=decision.side,
                score=decision.score,
            )

    def _evaluate_trapped_setups(
        self,
        *,
        ts_event: int,
        can_submit: bool,
    ) -> bool:
        """Advance every pending setup on the current completed minute."""
        if not self.trapped_setups:
            return False
        current = self._current_features(ts_event)
        released: list[_router.RouteDecision] = []
        released_symbols: set[str] = set()

        for symbol, setup in list(self.trapped_setups.items()):
            evaluation = _router.evaluate_trapped_release(
                setup=setup,
                bars=tuple(self.bars[symbol]),
                current_feature=current[symbol],
                route_config=self.route_config,
                trap_config=self.trap_config,
            )
            if evaluation.status == "PENDING":
                self.diagnostics["trapped_build_release_pending_checks"] += 1
                continue

            self.trapped_setups.pop(symbol, None)
            if evaluation.status == "EXPIRED":
                self.diagnostics["trapped_build_setups_expired"] += 1
            elif evaluation.status == "INVALIDATED":
                self.diagnostics["trapped_build_setups_invalidated"] += 1
            elif evaluation.status == "GEOMETRY_REJECTED":
                self.diagnostics["trapped_build_release_geometry_rejections"] += 1
            elif evaluation.status == "RELEASED" and evaluation.decision is not None:
                self.diagnostics["trapped_build_releases_confirmed"] += 1
                released.append(evaluation.decision)
                released_symbols.add(symbol)

            self._event(
                "TRAPPED_BUILD_STATE_TRANSITION",
                ts_event,
                symbol=symbol,
                status=evaluation.status,
                reason=evaluation.reason,
                setup_detected_ts=setup.detected_ts,
                setup_attack_side=setup.attack_side,
                diagnostics=dict(evaluation.diagnostics),
            )

        if not released:
            return False

        winner = _router.select_release_winner(
            released,
            self.route_config.ambiguity_score_gap,
        )
        if winner is None:
            self.diagnostics["trapped_build_release_global_ambiguity"] += 1
            self._event(
                "TRAPPED_BUILD_RELEASE_REJECTED",
                ts_event,
                reason="GLOBAL_OPPOSITE_RELEASE_AMBIGUITY",
                symbols=sorted(released_symbols),
            )
            return False
        if not can_submit:
            # A release is a new auction leg. If risk/funding/cooldown policy
            # blocks it at inception, it is consumed rather than chased later.
            self.diagnostics["trapped_build_release_blocked_no_chase"] += 1
            self._event(
                "TRAPPED_BUILD_RELEASE_REJECTED",
                ts_event,
                reason="RELEASE_INCEPTION_BLOCKED_NO_LATE_CHASE",
                symbol=winner.symbol,
                side=winner.side,
            )
            return False

        self._submit_decision(winner, ts_event)
        return bool(self.entry_pending)

    def _record_new_trapped_setups(
        self,
        *,
        pre_attack: dict[str, _router.FeatureObservation],
        attacks: dict[str, _router.FeatureObservation],
        interactions: dict[str, _router.FeatureObservation],
        confirmations: dict[str, _router.FeatureObservation],
        ts_event: int,
    ) -> None:
        for symbol in _v2._base.SYMBOLS:
            setup = _router.detect_trapped_build(
                symbol=symbol,
                bars=tuple(self.bars[symbol]),
                pre_attack_feature=pre_attack[symbol],
                attack_feature=attacks[symbol],
                interaction_feature=interactions[symbol],
                confirmation_feature=confirmations[symbol],
                route_config=self.route_config,
                trap_config=self.trap_config,
            )
            if setup is None:
                continue
            existing = self.trapped_setups.get(symbol)
            if existing is not None and existing.expires_ts >= ts_event:
                # Same symbol inside one setup lifetime is one causal episode.
                self.diagnostics["trapped_build_same_episode_suppressed"] += 1
                continue
            self.trapped_setups[symbol] = setup
            self.diagnostics["trapped_build_setups_detected"] += 1
            self.diagnostics["max_simultaneous_trapped_setups"] = max(
                int(self.diagnostics["max_simultaneous_trapped_setups"]),
                len(self.trapped_setups),
            )
            self._event(
                "TRAPPED_BUILD_SETUP_DETECTED",
                ts_event,
                symbol=symbol,
                attack_side=setup.attack_side,
                boundary=setup.boundary,
                attack_extreme=setup.attack_extreme,
                expires_ts=setup.expires_ts,
                setup_score=setup.setup_score,
                reasons=list(setup.reasons),
                diagnostics=dict(setup.diagnostics),
            )

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        """Advance live risk, V3 pending states and the quarter-hour router."""
        self.minute_index += 1
        self.diagnostics["complete_universe_minutes"] += 1
        self._record_equity(ts_event)

        open_symbols = [
            symbol
            for symbol in _v2._base.SYMBOLS
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
            self.trapped_setups.clear()
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
            if self.minute_index - self.entry_pending_minute > self.route_config.prior_bars:
                assert self.current_symbol is not None
                self.cancel_all_orders(self.instrument_ids[self.current_symbol])
                self.diagnostics["entry_expirations"] += 1
                self._event(
                    "ENTRY_EXPIRED",
                    ts_event,
                    reason="BOUNDARY_NOT_RETESTED_WITHIN_ONE_AUCTION",
                    validity_minutes=int(self.route_config.prior_bars),
                )
                self._clear_trade_state()
            return

        if not (self.config.evaluation_start_ns <= ts_event <= self.config.evaluation_end_ns):
            return

        funding_blocked = self._funding_blackout(ts_event)
        cooldown_blocked = (
            self.minute_index - self.last_entry_minute < self.config.cooldown_minutes
        )
        can_submit = not funding_blocked and not cooldown_blocked

        if self._evaluate_trapped_setups(
            ts_event=ts_event,
            can_submit=can_submit,
        ):
            return

        moment = datetime.fromtimestamp(ts_event / 1_000_000_000, tz=timezone.utc)
        if moment.minute % 15 != 2:
            return
        required = max(
            self.route_config.context_bars
            + self.route_config.prior_bars
            + self.route_config.response_bars,
            self.route_config.atr_period
            + self.route_config.prior_bars
            + self.route_config.response_bars
            + 1,
        )
        if any(len(self.bars[symbol]) < required for symbol in _v2._base.SYMBOLS):
            return

        attacks: dict[str, _router.FeatureObservation] = {}
        interactions: dict[str, _router.FeatureObservation] = {}
        confirmations: dict[str, _router.FeatureObservation] = {}
        pre_attack: dict[str, _router.FeatureObservation] = {}
        pre_offset = self.route_config.prior_bars + self.route_config.response_bars + 1
        for symbol in _v2._base.SYMBOLS:
            local_bars = list(self.bars[symbol])
            attack_ts = local_bars[-self.route_config.response_bars - 1].ts_event
            interaction_ts = local_bars[-self.route_config.response_bars].ts_event
            confirmation_ts = local_bars[-1].ts_event
            pre_attack_ts = local_bars[-pre_offset].ts_event
            attacks[symbol] = self.features[symbol].observation(
                attack_ts,
                self.config.feature_max_age_seconds,
            )
            interactions[symbol] = self.features[symbol].observation(
                interaction_ts,
                self.config.feature_max_age_seconds,
            )
            confirmations[symbol] = self.features[symbol].observation(
                confirmation_ts,
                self.config.feature_max_age_seconds,
            )
            pre_attack[symbol] = self.features[symbol].observation(
                pre_attack_ts,
                self.config.feature_max_age_seconds,
            )

        # Setup discovery remains useful during a funding/cooldown block; only
        # entry inception is prohibited. A later release must still be fresh.
        self._record_new_trapped_setups(
            pre_attack=pre_attack,
            attacks=attacks,
            interactions=interactions,
            confirmations=confirmations,
            ts_event=ts_event,
        )

        if not can_submit:
            return
        if not all(item.ready for item in interactions.values()):
            self.diagnostics["feature_stale_episodes"] += 1
            return
        if not all(item.ready for item in confirmations.values()):
            self.diagnostics["confirmation_feature_stale_episodes"] += 1
            return

        self.diagnostics["quarter_hour_decisions"] += 1
        winner, decisions = _router.route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol]) for symbol in _v2._base.SYMBOLS
            },
            features_by_symbol=interactions,
            confirmation_features_by_symbol=confirmations,
            config=self.route_config,
        )
        for decision in decisions.values():
            counts = self.diagnostics["route_counts"]
            counts[decision.state] = int(counts.get(decision.state, 0)) + 1
            if not decision.actionable and decision.reasons:
                reasons = self.diagnostics["unresolved_reason_counts"]
                reason = str(decision.reasons[0])
                reasons[reason] = int(reasons.get(reason, 0)) + 1
        if winner is None:
            self.diagnostics["unresolved_episodes"] += 1
            return
        self._submit_decision(winner, ts_event)


Candidate39Strategy = Candidate39V3Strategy
Candidate35Strategy = Candidate39V3Strategy
