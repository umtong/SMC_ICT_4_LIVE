"""All-candidate causal episode audit for Candidate 57's 4h jump family.

This strategy preserves the exact Nautilus-traded candidate while observing all
four source candidates at every completed 4h boundary, including candidates
hidden by an occupied global account slot. Each candidate is shadowed with the
same structural stop, transient BE arm/escape ordering and 240-minute source
horizon. The shadow is diagnostic only: it does not size capital, submit orders
or replace Nautilus account/matching.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
from typing import Any

from router import FeatureObservation, RouteDecision, route_universe
from strategy_base import SYMBOLS
from strategy_jump_transient import (
    Candidate35Config as _JumpConfig,
    Candidate35Strategy as _JumpStrategy,
)


@dataclass(slots=True)
class ShadowEpisode:
    candidate_id: str
    symbol: str
    side: int
    episode_ts: int
    source_minute_index: int
    entry: float
    stop: float
    target: float
    planned_loss_per_unit: float
    estimated_cost_per_unit: float
    router_selected: bool
    router_score: float
    candidate_count_at_boundary: int
    slot_state_at_boundary: str
    actual_symbol_at_boundary: str | None
    entry_pending_at_boundary: bool
    diagnostics: dict[str, Any]
    protection_active: bool = False
    protection_escaped: bool = False
    protection_floor_price: float | None = None
    protection_activation_minute: int | None = None
    protection_escape_minute: int | None = None
    favorable_price: float | None = None
    peak_net_r: float = -math.inf
    mae_fraction: float = 0.0
    mfe_fraction: float = 0.0
    outcome: str | None = None
    exit_ts: int | None = None
    exit_price_reference: float | None = None
    exit_net_r: float | None = None
    elapsed_minutes: int | None = None
    censored: bool = False
    path_marks: dict[str, float] = field(default_factory=dict)

    @property
    def active(self) -> bool:
        return self.outcome is None and not self.censored


class Candidate35Config(_JumpConfig, frozen=True):
    jump_audit_enabled: bool = True


class Candidate35Strategy(_JumpStrategy):
    """Exact traded jump policy plus non-trading all-candidate audit."""

    def __init__(self, config: Candidate35Config) -> None:
        super().__init__(config)
        self.audit_episodes: list[ShadowEpisode] = []
        self.audit_active: dict[str, ShadowEpisode] = {}
        self.audit_boundary_count = 0
        self.audit_candidate_count = 0
        self.audit_collision_boundaries = 0
        self.audit_censored_count = 0
        self.audit_duplicate_keys = 0
        self.diagnostics.update(
            {
                "jump_audit_enabled": bool(config.jump_audit_enabled),
                "jump_audit_boundaries": 0,
                "jump_audit_all_candidates": 0,
                "jump_audit_collision_boundaries": 0,
                "jump_audit_censored": 0,
                "jump_audit_duplicate_keys": 0,
            }
        )

    def _slot_state(self) -> tuple[str, str | None]:
        open_symbols = [
            symbol
            for symbol in SYMBOLS
            if not self.portfolio.is_flat(self.instrument_ids[symbol])
        ]
        if open_symbols:
            return "OPEN_POSITION", open_symbols[0]
        if self.entry_pending:
            return "ENTRY_PENDING", self.current_symbol
        if self.pending_jump is not None:
            return "PENDING_CONFIRMATION", self.pending_jump.decision.symbol
        return "FLAT", None

    def _planned_geometry(self, decision: RouteDecision) -> tuple[float, float]:
        entry = float(decision.entry_reference)
        stop = float(decision.stop_reference)
        side = int(decision.side)
        fee_rate = float(self.config.all_in_cost_bps_each_side) / 10_000.0
        slippage_rate = float(self.config.adverse_slippage_bps_each_side) / 10_000.0
        funding_rate = float(self.config.funding_reserve_bps) / 10_000.0
        adverse_entry = entry * (1.0 + side * slippage_rate)
        adverse_stop = stop * (1.0 - side * slippage_rate)
        planned = (
            abs(adverse_entry - adverse_stop)
            + fee_rate * (abs(adverse_entry) + abs(adverse_stop))
            + funding_rate * abs(entry)
        )
        price_distance = abs(entry - stop)
        estimated_cost = max(0.0, planned - price_distance)
        return planned, estimated_cost

    def _close_shadow(
        self,
        episode: ShadowEpisode,
        *,
        outcome: str,
        ts_event: int,
        exit_price: float,
        elapsed: int,
    ) -> None:
        gross = episode.side * (float(exit_price) - episode.entry)
        net_r = (
            (gross - episode.estimated_cost_per_unit)
            / episode.planned_loss_per_unit
        )
        episode.outcome = outcome
        episode.exit_ts = int(ts_event)
        episode.exit_price_reference = float(exit_price)
        episode.exit_net_r = float(net_r)
        episode.elapsed_minutes = int(elapsed)
        self.audit_active.pop(episode.candidate_id, None)

    def _update_one_shadow(self, episode: ShadowEpisode, ts_event: int) -> None:
        if not episode.active or int(ts_event) <= int(episode.episode_ts):
            return
        bar = self.bars[episode.symbol][-1]
        elapsed = max(
            1,
            int(round((int(ts_event) - int(episode.episode_ts)) / 60_000_000_000)),
        )
        side = episode.side
        entry = episode.entry

        # A floor armed on a previous completed minute is tested before this
        # bar can update favorable excursion or disarm the protection.
        if episode.protection_active and episode.protection_floor_price is not None:
            floor = float(episode.protection_floor_price)
            crossed = float(bar.low) <= floor if side > 0 else float(bar.high) >= floor
            if crossed:
                self._close_shadow(
                    episode,
                    outcome="TRANSIENT_BE_EXIT",
                    ts_event=ts_event,
                    exit_price=floor,
                    elapsed=elapsed,
                )
                return

        # Conservative same-bar ambiguity: structural invalidation is tested
        # before the distant emergency target.
        stop_hit = (
            float(bar.low) <= episode.stop
            if side > 0
            else float(bar.high) >= episode.stop
        )
        if stop_hit:
            self._close_shadow(
                episode,
                outcome="STRUCTURAL_STOP",
                ts_event=ts_event,
                exit_price=episode.stop,
                elapsed=elapsed,
            )
            return

        target_hit = (
            float(bar.high) >= episode.target
            if side > 0
            else float(bar.low) <= episode.target
        )
        if target_hit:
            self._close_shadow(
                episode,
                outcome="EMERGENCY_TARGET",
                ts_event=ts_event,
                exit_price=episode.target,
                elapsed=elapsed,
            )
            return

        favorable = float(bar.high) if side > 0 else float(bar.low)
        adverse = float(bar.low) if side > 0 else float(bar.high)
        favorable_fraction = side * (favorable - entry) / entry
        adverse_fraction = side * (adverse - entry) / entry
        episode.mfe_fraction = max(episode.mfe_fraction, favorable_fraction)
        episode.mae_fraction = min(episode.mae_fraction, adverse_fraction)

        if episode.favorable_price is None:
            episode.favorable_price = favorable
        else:
            episode.favorable_price = (
                max(float(episode.favorable_price), favorable)
                if side > 0
                else min(float(episode.favorable_price), favorable)
            )
        gross_favorable = side * (float(episode.favorable_price) - entry)
        peak_net_r = (
            gross_favorable - episode.estimated_cost_per_unit
        ) / episode.planned_loss_per_unit
        episode.peak_net_r = max(episode.peak_net_r, peak_net_r)

        arm_r = float(self.config.jump_protection_activation_r)
        escape_r = float(self.config.jump_protection_escape_r)
        if (
            not episode.protection_escaped
            and math.isfinite(escape_r)
            and episode.peak_net_r >= escape_r
        ):
            episode.protection_escaped = True
            episode.protection_escape_minute = elapsed
            episode.protection_active = False
            episode.protection_floor_price = None
        elif (
            not episode.protection_escaped
            and math.isfinite(arm_r)
            and episode.peak_net_r >= arm_r
            and not episode.protection_active
        ):
            episode.protection_active = True
            episode.protection_activation_minute = elapsed
            episode.protection_floor_price = (
                entry + side * episode.estimated_cost_per_unit
            )

        for minute in (5, 15, 30, 60, 120, 240):
            key = str(minute)
            if elapsed >= minute and key not in episode.path_marks:
                episode.path_marks[key] = side * (float(bar.close) - entry) / entry

        if elapsed >= int(self.route_config.jump_timeframe_minutes):
            self._close_shadow(
                episode,
                outcome="SOURCE_HORIZON",
                ts_event=ts_event,
                exit_price=float(bar.close),
                elapsed=elapsed,
            )

    def _update_shadow_paths(self, ts_event: int) -> None:
        for episode in list(self.audit_active.values()):
            self._update_one_shadow(episode, ts_event)

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
        ]
        self.audit_boundary_count += 1
        if len(candidates) > 1:
            self.audit_collision_boundaries += 1
        slot_state, actual_symbol = self._slot_state()
        selected_symbol = winner.symbol if winner is not None else None

        for decision in candidates:
            candidate_id = f"{decision.symbol}:{int(decision.episode_ts)}"
            if any(item.candidate_id == candidate_id for item in self.audit_episodes):
                self.audit_duplicate_keys += 1
                continue
            planned, estimated_cost = self._planned_geometry(decision)
            if not math.isfinite(planned) or planned <= 0.0:
                continue
            episode = ShadowEpisode(
                candidate_id=candidate_id,
                symbol=decision.symbol,
                side=int(decision.side),
                episode_ts=int(decision.episode_ts),
                source_minute_index=int(self.minute_index + 1),
                entry=float(decision.entry_reference),
                stop=float(decision.stop_reference),
                target=float(decision.objective_reference),
                planned_loss_per_unit=float(planned),
                estimated_cost_per_unit=float(estimated_cost),
                router_selected=decision.symbol == selected_symbol,
                router_score=float(decision.score),
                candidate_count_at_boundary=len(candidates),
                slot_state_at_boundary=slot_state,
                actual_symbol_at_boundary=actual_symbol,
                entry_pending_at_boundary=bool(self.entry_pending),
                diagnostics=dict(decision.diagnostics),
            )
            self.audit_episodes.append(episode)
            self.audit_active[candidate_id] = episode
            self.audit_candidate_count += 1

    def _on_complete_universe_minute(self, ts_event: int) -> None:
        # Observe all candidates before the traded strategy can return early
        # because the global slot is occupied.
        self._update_shadow_paths(ts_event)
        self._audit_boundary(ts_event)
        super()._on_complete_universe_minute(ts_event)

    @staticmethod
    def _episode_dict(episode: ShadowEpisode) -> dict[str, Any]:
        return {
            "candidate_id": episode.candidate_id,
            "symbol": episode.symbol,
            "side": episode.side,
            "episode_ts": episode.episode_ts,
            "source_minute_index": episode.source_minute_index,
            "entry": episode.entry,
            "stop": episode.stop,
            "target": episode.target,
            "planned_loss_per_unit": episode.planned_loss_per_unit,
            "estimated_cost_per_unit": episode.estimated_cost_per_unit,
            "router_selected": episode.router_selected,
            "router_score": episode.router_score,
            "candidate_count_at_boundary": episode.candidate_count_at_boundary,
            "slot_state_at_boundary": episode.slot_state_at_boundary,
            "actual_symbol_at_boundary": episode.actual_symbol_at_boundary,
            "entry_pending_at_boundary": episode.entry_pending_at_boundary,
            "diagnostics": episode.diagnostics,
            "protection_active": episode.protection_active,
            "protection_escaped": episode.protection_escaped,
            "protection_floor_price": episode.protection_floor_price,
            "protection_activation_minute": episode.protection_activation_minute,
            "protection_escape_minute": episode.protection_escape_minute,
            "peak_net_r": (
                episode.peak_net_r
                if math.isfinite(episode.peak_net_r)
                else None
            ),
            "mfe_fraction": episode.mfe_fraction,
            "mae_fraction": episode.mae_fraction,
            "outcome": episode.outcome,
            "exit_ts": episode.exit_ts,
            "exit_price_reference": episode.exit_price_reference,
            "exit_net_r": episode.exit_net_r,
            "elapsed_minutes": episode.elapsed_minutes,
            "censored": episode.censored,
            "path_marks": episode.path_marks,
        }

    def on_stop(self) -> None:
        latest_ts = self._latest_ts()
        for episode in list(self.audit_active.values()):
            episode.censored = True
            episode.outcome = "CENSORED_EVALUATION_END"
            episode.exit_ts = latest_ts
            self.audit_active.pop(episode.candidate_id, None)
            self.audit_censored_count += 1

        self.diagnostics["jump_audit_boundaries"] = self.audit_boundary_count
        self.diagnostics["jump_audit_all_candidates"] = self.audit_candidate_count
        self.diagnostics[
            "jump_audit_collision_boundaries"
        ] = self.audit_collision_boundaries
        self.diagnostics["jump_audit_censored"] = self.audit_censored_count
        self.diagnostics["jump_audit_duplicate_keys"] = self.audit_duplicate_keys

        super().on_stop()

        destination = Path(self.config.output_dir)
        destination.mkdir(parents=True, exist_ok=True)
        payload = [self._episode_dict(item) for item in self.audit_episodes]
        (destination / "jump_candidate_audit.json").write_text(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
                allow_nan=False,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )


__all__ = ["Candidate35Config", "Candidate35Strategy"]
