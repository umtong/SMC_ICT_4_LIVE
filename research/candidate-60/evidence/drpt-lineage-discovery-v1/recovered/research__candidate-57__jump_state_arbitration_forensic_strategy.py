"""All-source-candidate audit for the frozen jump state × arbitration policy."""
from __future__ import annotations

from router import FeatureObservation, route_universe as combined_route_universe
from router_jump_base import route_universe as source_route_universe
from strategy_base import SYMBOLS
from strategy_jump_audit_base import (
    Candidate35Config as _AuditConfig,
    Candidate35Strategy as _AuditStrategy,
    ShadowEpisode,
)


class Candidate35Config(_AuditConfig, frozen=True):
    pass


class Candidate35Strategy(_AuditStrategy):
    """Trade the combined policy while shadowing every raw source candidate."""

    def _audit_boundary(self, ts_event: int) -> None:
        if not bool(self.config.jump_audit_enabled):
            return
        if not (
            self.config.evaluation_start_ns
            <= ts_event
            <= self.config.evaluation_end_ns
        ):
            return
        timeframe = int(self.route_config.jump_timeframe_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        if minute_ordinal % timeframe != timeframe - 1:
            return
        required = timeframe * (int(self.route_config.jump_volatility_window) + 2)
        if any(len(self.bars[symbol]) < required for symbol in SYMBOLS):
            return

        observations = {
            symbol: FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        bars_by_symbol = {
            symbol: tuple(self.bars[symbol])
            for symbol in SYMBOLS
        }
        source_winner, source_decisions = source_route_universe(
            bars_by_symbol=bars_by_symbol,
            features_by_symbol=observations,
            config=self.route_config,
        )
        combined_winner, combined_decisions = combined_route_universe(
            bars_by_symbol=bars_by_symbol,
            features_by_symbol=observations,
            config=self.route_config,
        )
        source_candidates = [
            decision for decision in source_decisions.values() if decision.actionable
        ]
        self.audit_boundary_count += 1
        if len(source_candidates) > 1:
            self.audit_collision_boundaries += 1
        slot_state, actual_symbol = self._slot_state()
        selected_symbol = (
            combined_winner.symbol if combined_winner is not None else None
        )

        for source_decision in source_candidates:
            candidate_id = (
                f"{source_decision.symbol}:{int(source_decision.episode_ts)}"
            )
            if any(item.candidate_id == candidate_id for item in self.audit_episodes):
                self.audit_duplicate_keys += 1
                continue
            combined_decision = combined_decisions.get(source_decision.symbol)
            combined_actionable = bool(
                combined_decision is not None and combined_decision.actionable
            )
            diagnostics = dict(source_decision.diagnostics or {})
            if combined_decision is not None:
                diagnostics.update(dict(combined_decision.diagnostics or {}))
                diagnostics["combined_policy_state"] = str(combined_decision.state)
                diagnostics["combined_policy_reasons"] = list(
                    combined_decision.reasons
                )
            diagnostics.update(
                {
                    "raw_source_candidate": 1,
                    "combined_policy_accepted": int(combined_actionable),
                    # Preserve compatibility with the earlier taker-state audit.
                    "taker_filter_accepted": int(combined_actionable),
                    "source_router_selected": int(
                        source_winner is not None
                        and source_decision.symbol == source_winner.symbol
                    ),
                }
            )

            planned, estimated_cost = self._planned_geometry(source_decision)
            if planned <= 0.0:
                continue
            router_score = float(source_decision.score)
            if combined_actionable and combined_decision is not None:
                router_score = float(combined_decision.score)
            episode = ShadowEpisode(
                candidate_id=candidate_id,
                symbol=source_decision.symbol,
                side=int(source_decision.side),
                episode_ts=int(source_decision.episode_ts),
                source_minute_index=int(self.minute_index + 1),
                entry=float(source_decision.entry_reference),
                stop=float(source_decision.stop_reference),
                target=float(source_decision.objective_reference),
                planned_loss_per_unit=float(planned),
                estimated_cost_per_unit=float(estimated_cost),
                router_selected=(
                    combined_actionable
                    and source_decision.symbol == selected_symbol
                ),
                router_score=router_score,
                candidate_count_at_boundary=len(source_candidates),
                slot_state_at_boundary=slot_state,
                actual_symbol_at_boundary=actual_symbol,
                entry_pending_at_boundary=bool(self.entry_pending),
                diagnostics=diagnostics,
            )
            self.audit_episodes.append(episode)
            self.audit_active[candidate_id] = episode
            self.audit_candidate_count += 1


__all__ = ["Candidate35Config", "Candidate35Strategy"]
