"""Causal source-horizon boundary handoff for the 4h jump family.

The reused one-slot strategy managed an open position and returned before
routing a newly completed 4h signal.  When the old position reached its source
horizon exactly on that boundary, the new independent boundary was silently
lost.  This adapter can freeze that new decision, close the old position, and
submit the frozen decision only after the Nautilus account is flat on a later
minute.

No simultaneous position is allowed.  The frozen decision expires after three
complete minutes so a delayed flatten cannot turn it into a stale entry.
"""
from __future__ import annotations

from dataclasses import dataclass

from router import FeatureObservation, RouteDecision, route_universe
from strategy_base import SYMBOLS
from strategy_jump_base import (
    Candidate35Config as _JumpConfig,
    Candidate35Strategy as _JumpStrategy,
)


@dataclass(slots=True)
class BoundaryHandoff:
    decision: RouteDecision
    source_minute_index: int
    source_exit_minute_index: int
    expires_minute_index: int


class Candidate35Config(_JumpConfig, frozen=True):
    jump_boundary_handoff_enabled: bool = False
    jump_boundary_handoff_expiry_minutes: int = 3


class Candidate35Strategy(_JumpStrategy):
    """Exact jump policy with optional flat-account boundary handoff."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.boundary_handoff: BoundaryHandoff | None = None
        self.diagnostics.update(
            {
                "jump_boundary_handoff_enabled": bool(
                    config.jump_boundary_handoff_enabled
                ),
                "jump_boundary_handoff_expiry_minutes": int(
                    config.jump_boundary_handoff_expiry_minutes
                ),
                "jump_boundary_handoff_frozen": 0,
                "jump_boundary_handoff_submitted": 0,
                "jump_boundary_handoff_expired": 0,
                "jump_boundary_handoff_no_candidate": 0,
                "jump_boundary_handoff_still_open": 0,
            }
        )

    def _at_source_boundary(self, ts_event: int) -> bool:
        timeframe = int(self.route_config.jump_timeframe_minutes)
        minute_ordinal = int(ts_event // 60_000_000_000)
        return minute_ordinal % timeframe == timeframe - 1

    def _route_handoff_boundary(self, ts_event: int) -> RouteDecision | None:
        if not self._at_source_boundary(ts_event):
            return None
        required = int(self.route_config.jump_timeframe_minutes) * (
            int(self.route_config.jump_volatility_window) + 2
        )
        if any(len(self.bars[symbol]) < required for symbol in SYMBOLS):
            return None
        observations = {
            symbol: FeatureObservation(
                observed_time_ns=int(self.bars[symbol][-1].ts_event),
                ready=True,
            )
            for symbol in SYMBOLS
        }
        winner, decisions = route_universe(
            bars_by_symbol={
                symbol: tuple(self.bars[symbol])
                for symbol in SYMBOLS
            },
            features_by_symbol=observations,
            config=self.route_config,
        )
        candidates = [
            decision
            for decision in decisions.values()
            if decision.actionable
            and (decision.symbol, int(decision.episode_ts))
            not in self.used_episode_keys
        ]
        self.diagnostics["jump_source_candidates"] += len(candidates)
        if winner is None or not candidates:
            self.diagnostics["jump_boundary_handoff_no_candidate"] += 1
            return None
        return next(
            (candidate for candidate in candidates if candidate.symbol == winner.symbol),
            winner,
        )

    def _maybe_freeze_boundary_handoff(self, ts_event: int) -> None:
        if not bool(self.config.jump_boundary_handoff_enabled):
            return
        if self.boundary_handoff is not None:
            return
        scenario = self.current_scenario
        if scenario is None:
            return
        source_exit = int(scenario.get("source_exit_minute_index", 2**63 - 1))
        # Only the exact source-horizon boundary is repaired.  We do not route
        # arbitrary new signals while an earlier position remains active.
        if self.minute_index < source_exit or not self._at_source_boundary(ts_event):
            return
        decision = self._route_handoff_boundary(ts_event)
        if decision is None:
            return
        timeframe = int(self.route_config.jump_timeframe_minutes)
        expiry = self.minute_index + max(
            1, int(self.config.jump_boundary_handoff_expiry_minutes)
        )
        self.boundary_handoff = BoundaryHandoff(
            decision=decision,
            source_minute_index=self.minute_index,
            source_exit_minute_index=self.minute_index + timeframe,
            expires_minute_index=expiry,
        )
        self.used_episode_keys.add((decision.symbol, int(decision.episode_ts)))
        self.diagnostics["jump_boundary_handoff_frozen"] += 1
        self._event(
            "JUMP_BOUNDARY_HANDOFF_FROZEN",
            ts_event,
            symbol=decision.symbol,
            episode_ts=int(decision.episode_ts),
            arbitration_mode=(decision.diagnostics or {}).get(
                "jump_effective_arbitration_mode",
                (decision.diagnostics or {}).get("selection_mode", "source"),
            ),
            source_exit_minute_index=self.boundary_handoff.source_exit_minute_index,
            expires_minute_index=expiry,
        )

    def _maybe_submit_boundary_handoff(self, ts_event: int) -> None:
        handoff = self.boundary_handoff
        if handoff is None:
            return
        if self.minute_index > handoff.expires_minute_index:
            self.diagnostics["jump_boundary_handoff_expired"] += 1
            self._event(
                "JUMP_BOUNDARY_HANDOFF_EXPIRED",
                ts_event,
                symbol=handoff.decision.symbol,
                episode_ts=int(handoff.decision.episode_ts),
            )
            self.boundary_handoff = None
            return
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        if open_symbols or self.entry_pending or self.pending_jump is not None:
            self.diagnostics["jump_boundary_handoff_still_open"] += 1
            return
        self.boundary_handoff = None
        before = int(self.diagnostics["entry_submissions"])
        self._submit_source_decision(
            handoff.decision,
            ts_event,
            source_exit_minute=handoff.source_exit_minute_index,
        )
        if int(self.diagnostics["entry_submissions"]) > before:
            self.diagnostics["jump_boundary_handoff_submitted"] += 1
            if self.current_scenario is not None:
                self.current_scenario["boundary_handoff"] = True
                self.current_scenario[
                    "boundary_handoff_delay_minutes"
                ] = self.minute_index - handoff.source_minute_index
            self._event(
                "JUMP_BOUNDARY_HANDOFF_SUBMITTED",
                ts_event,
                symbol=handoff.decision.symbol,
                episode_ts=int(handoff.decision.episode_ts),
                delay_minutes=self.minute_index - handoff.source_minute_index,
            )

    def _manage_open_position(self, ts_event: int) -> None:
        self._maybe_freeze_boundary_handoff(ts_event)
        super()._manage_open_position(ts_event)

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        super()._on_complete_universe_minute(ts_event)
        # The base strategy has now observed any exit fill and cleared old
        # scenario state.  Submission happens only if the account is actually
        # flat; no custom portfolio or matching logic is introduced.
        self._maybe_submit_boundary_handoff(ts_event)


__all__ = ["Candidate35Config", "Candidate35Strategy"]
