"""Fresh initiative acceptance on the inherited Nautilus/shared-slot path.

A completed event bar freezes a past-known liquidity target, structural stop,
and cost-aware entry cap. Only a strictly later completed bar may confirm
acceptance beyond the event extreme. Execution, fills, fees, positions, margin,
and NAV remain owned by NautilusTrader and the existing shared-account layer.
"""
from __future__ import annotations

from dataclasses import asdict
import math
from typing import Any

from flow_inflection_logic import has_adverse_slippage_room, worst_entry_preserving_net_r
from fresh_initiative_router import (
    FreshDecision,
    FreshEpisode,
    FreshEvidence,
    FreshObservation,
    FreshThresholds,
    advance_fresh_episode,
    classify_fresh_initiative,
)
from logic import net_r_at_price, planned_loss_per_unit
from strategy_base import PendingSetup, _as_float
from strategy_v9 import ArmedEntryPath
from strategy_v26 import ScenarioValidEntryStrategy
from strategy_v41_competing_auction import _construct

FRESH_BRANCH = "FRESH_INITIATIVE_ACCEPTANCE"


class FreshInitiativeAcceptanceMixin:
    def __init__(self, config: Any) -> None:
        self.fresh_thresholds = FreshThresholds()
        self.fresh_episode: FreshEpisode | None = None
        super().__init__(config)  # type: ignore[misc]
        self.diagnostics.update({
            "candidate21_fresh_state_evaluations": 0,
            "candidate21_fresh_events": 0,
            "candidate21_fresh_no_natural_target": 0,
            "candidate21_fresh_event_geometry_rejected": 0,
            "candidate21_fresh_episodes_armed": 0,
            "candidate21_fresh_confirmations": 0,
            "candidate21_fresh_invalidated": 0,
            "candidate21_fresh_target_consumed": 0,
            "candidate21_fresh_expired": 0,
            "candidate21_fresh_target_source_expired": 0,
            "candidate21_fresh_confirmation_geometry_rejected": 0,
            "candidate21_fresh_submissions": 0,
        })

    def _clear_trade_state(self) -> None:
        super()._clear_trade_state()  # type: ignore[misc]
        self.fresh_episode = None

    def _inputs_ready(self) -> bool:
        if self._feature("metrics_ready") <= 0.5 or self._feature("basis_ready") <= 0.5:
            return False
        if self._feature("depth_data_gap") > 0.5:
            return False
        names = (
            "flow_60s", "flow_price_alignment_60s", "notional_burst",
            "efficiency_60s", "depth_imbalance_1", "oi_change_5m",
            "premium_change_5m", "premium_index",
        )
        return all(math.isfinite(self._feature(name)) for name in names)

    def _target_pools(self, side: int, high: float, low: float):
        if side > 0:
            pools = [p for p in self.active_pools.values() if p.kind == "HIGH" and p.level > high]
            return sorted(pools, key=lambda p: p.level)
        pools = [p for p in self.active_pools.values() if p.kind == "LOW" and p.level < low]
        return sorted(pools, key=lambda p: p.level, reverse=True)

    def _geometry(self, *, side: int, observed: float, stop: float, target: float):
        cost = self.config.all_in_cost_bps_each_side / 10_000.0
        slip = self.config.adverse_slippage_bps_each_side / 10_000.0
        bound = worst_entry_preserving_net_r(
            stop=stop, target=target, side=side,
            minimum_net_r=self.config.min_target_net_r,
            cost_rate=cost, adverse_slippage_rate=slip,
        )
        if not math.isfinite(bound):
            return None
        increment = _as_float(self.instrument.price_increment)
        price = self.instrument.make_price(bound)
        entry = _as_float(price)
        if side > 0 and entry > bound:
            price = self.instrument.make_price(bound - increment)
            entry = _as_float(price)
        elif side < 0 and entry < bound:
            price = self.instrument.make_price(bound + increment)
            entry = _as_float(price)
        valid = (
            stop < observed <= entry < target
            if side > 0 else target < entry <= observed < stop
        )
        if not valid or not has_adverse_slippage_room(
            observed_price=observed, limit_price=entry, side=side,
            adverse_slippage_rate=slip,
        ):
            return None
        loss = planned_loss_per_unit(entry, stop, side, cost, slip)
        if not math.isfinite(loss) or loss <= 0.0:
            return None
        target_r = net_r_at_price(entry, target, side, loss, cost)
        return (price, entry, loss, target_r) if target_r + 1e-9 >= self.config.min_target_net_r else None

    def _detect_sweep(self, row: dict[str, float | int], previous_close: float) -> None:
        del previous_close
        if self.pending is not None or self.fresh_episode is not None:
            return
        self.diagnostics["candidate21_fresh_state_evaluations"] += 1
        if not self._inputs_ready() or len(self.bars) < 31:
            return
        atr = float(self._atr())
        close, prior = float(row["close"]), float(self.bars[-31]["close"])
        if not all(math.isfinite(v) and v > 0.0 for v in (atr, close, prior)):
            return
        evidence = FreshEvidence(
            flow_60s=self._feature("flow_60s"),
            flow_price_alignment_60s=self._feature("flow_price_alignment_60s"),
            notional_burst=self._feature("notional_burst"),
            efficiency_60s=self._feature("efficiency_60s"),
            depth_imbalance_1=self._feature("depth_imbalance_1"),
            oi_change_5m=self._feature("oi_change_5m"),
            premium_change_5m=self._feature("premium_change_5m"),
            premium_index=self._feature("premium_index"),
            prior_30m_return_bps=math.log(close / prior) * 10_000.0,
        )
        signal = classify_fresh_initiative(evidence, self.fresh_thresholds)
        if signal.side == 0:
            return
        self.diagnostics["candidate21_fresh_events"] += 1
        self.scenario_counter += 1
        scenario_id = f"c21-fresh-{self.scenario_counter:07d}"
        side, high, low = signal.side, float(row["high"]), float(row["low"])
        pools = self._target_pools(side, high, low)
        details = {"side": side, "event_index": self.bar_index, **asdict(evidence)}
        if not pools:
            self.diagnostics["candidate21_fresh_no_natural_target"] += 1
            self._transition(scenario_id, "FRESH_EVENT_UNRESOLVED", int(row["ts"]), int(row["ts"]),
                             "CLOSED", "NO_PAST_KNOWN_OPPOSING_TARGET", close, details)
            return
        recent = list(self.bars)[-3:]
        stop_raw = (
            min(float(x["low"]) for x in recent) - self.config.stop_buffer_atr * atr
            if side > 0 else max(float(x["high"]) for x in recent) + self.config.stop_buffer_atr * atr
        )
        stop = _as_float(self.instrument.make_price(stop_raw))
        selected = None
        for candidate in pools:
            candidate_target = _as_float(self.instrument.make_price(candidate.level))
            candidate_geometry = self._geometry(
                side=side, observed=high if side > 0 else low,
                stop=stop, target=candidate_target,
            )
            if candidate_geometry is not None:
                selected = (candidate, candidate_target, candidate_geometry)
                break
        if selected is None:
            self.diagnostics["candidate21_fresh_event_geometry_rejected"] += 1
            self._transition(scenario_id, "FRESH_EVENT_UNRESOLVED", int(row["ts"]), int(row["ts"]),
                             "CLOSED", "NO_OPPOSING_POOL_PRESERVES_POST_COST_GEOMETRY", close,
                             {**details, "stop": stop, "opposing_pool_count": len(pools)})
            return
        pool, target, geometry = selected
        _, event_cap, event_loss, event_r = geometry
        origin = float(self.bars[-2]["close"])
        details.update({
            "event_open": float(row["open"]), "event_high": high, "event_low": low,
            "event_close": close, "event_origin": origin, "stop": stop, "target": target,
            "target_pool_id": pool.pool_id, "target_pool_source": pool.source,
            "event_entry_cap": event_cap, "event_planned_loss": event_loss,
            "event_target_net_r": event_r, "event_reason": signal.reason,
        })
        episode = FreshEpisode(
            scenario_id=scenario_id, side=side, event_index=self.bar_index,
            expires_index=self.bar_index + self.fresh_thresholds.maximum_wait_bars,
            event_high=high, event_low=low, event_close=close, origin_price=origin,
            stop_price=stop, target_price=target, target_pool_id=pool.pool_id,
        )
        self.pending = _construct(
            PendingSetup, scenario_id=scenario_id, branch=FRESH_BRANCH, side=side,
            swept_kind="LOW" if side > 0 else "HIGH", pool_id=pool.pool_id,
            pool_level=target, created_index=self.bar_index, created_ts=int(row["ts"]),
            expires_index=episode.expires_index, sweep_extreme=low if side > 0 else high,
            structure=high if side > 0 else low, atr=atr, hold_count=0,
            retrace_armed=False, details=details,
        )
        self.fresh_episode = episode
        self.diagnostics["candidate21_fresh_episodes_armed"] += 1
        self._transition(scenario_id, "FRESH_EVENT_ARMED", int(row["ts"]), int(row["ts"]),
                         "AWAIT_LATER_ACCEPTANCE", signal.reason, close, details)

    def _process_pending(self, row: dict[str, float | int]) -> bool:
        if self.pending is not None and self.pending.branch == FRESH_BRANCH:
            return self._advance_fresh(row)
        return super()._process_pending(row)  # type: ignore[misc]

    def _close_fresh(self, key: str) -> None:
        self.diagnostics[key] += 1
        self.pending = None
        self.fresh_episode = None

    def _advance_fresh(self, row: dict[str, float | int]) -> bool:
        setup, episode = self.pending, self.fresh_episode
        if setup is None or episode is None:
            self.pending = None
            self.fresh_episode = None
            return True
        advanced = advance_fresh_episode(
            episode,
            FreshObservation(self.bar_index, float(row["open"]), float(row["high"]),
                             float(row["low"]), float(row["close"])),
            self.fresh_thresholds,
        )
        if advanced is episode:
            return True
        self.fresh_episode = advanced
        setup.details["latest_fresh_state"] = {**asdict(advanced), "decision": advanced.decision.value}
        next_state = "AWAIT_LATER_ACCEPTANCE" if advanced.decision is FreshDecision.WAITING else (
            "ACCEPTANCE_CONFIRMED" if advanced.decision is FreshDecision.CONFIRMED else "CLOSED"
        )
        self._transition(setup.scenario_id, "FRESH_EVENT_OBSERVED", int(row["ts"]), int(row["ts"]),
                         next_state, advanced.reason, float(row["close"]), setup.details)
        if advanced.decision is FreshDecision.WAITING:
            return True
        terminal_keys = {
            FreshDecision.INVALIDATED: "candidate21_fresh_invalidated",
            FreshDecision.TARGET_CONSUMED: "candidate21_fresh_target_consumed",
            FreshDecision.EXPIRED: "candidate21_fresh_expired",
        }
        if advanced.decision in terminal_keys:
            self._close_fresh(terminal_keys[advanced.decision])
            return True
        if advanced.target_pool_id not in self.active_pools:
            self._transition(setup.scenario_id, "FRESH_EVENT_UNRESOLVED", int(row["ts"]), int(row["ts"]),
                             "CLOSED", "TARGET_SOURCE_EXPIRED", float(row["close"]), setup.details)
            self._close_fresh("candidate21_fresh_target_source_expired")
            return True
        self.diagnostics["candidate21_fresh_confirmations"] += 1
        observed = _as_float(self.instrument.make_price(float(row["close"])))
        geometry = self._geometry(side=advanced.side, observed=observed,
                                  stop=advanced.stop_price, target=advanced.target_price)
        if geometry is None:
            self._transition(setup.scenario_id, "FRESH_EVENT_UNRESOLVED", int(row["ts"]), int(row["ts"]),
                             "CLOSED", "CONFIRMATION_CONSUMED_COST_GEOMETRY", observed, setup.details)
            self._close_fresh("candidate21_fresh_confirmation_geometry_rejected")
            return True
        entry_price, entry, loss, target_r = geometry
        armed = ArmedEntryPath(
            setup=setup, flow_state="STRICTLY_LATER_ACCEPTANCE", choch_close=observed,
            stop=advanced.stop_price, atr=setup.atr, created_index=self.bar_index,
            created_ts=int(row["ts"]), details={**setup.details, "confirmation_index": self.bar_index,
                                                "confirmation_close": observed},
        )
        self.pending = None
        self.fresh_episode = None
        self.armed_entry_path = armed
        submitted = self._submit_price_capped_bracket(
            armed=armed, row=row, entry_price=entry_price,
            stop_price=self.instrument.make_price(advanced.stop_price),
            target_price=self.instrument.make_price(advanced.target_price),
            sizing_entry=entry, planned_loss=loss,
            target_source=f"POOL:{advanced.target_pool_id}", target_r=target_r,
            branch=FRESH_BRANCH, event_type="FRESH_ACCEPTANCE_LIMIT_SUBMITTED",
            reason=advanced.reason, expires_index=self.bar_index + 3,
            entry_tag="CANDIDATE21_FRESH_ACCEPTANCE",
            extra={"event_index": advanced.event_index, "confirmation_index": self.bar_index,
                   "frozen_origin": advanced.origin_price, "frozen_stop": advanced.stop_price,
                   "frozen_target": advanced.target_price},
        )
        if submitted:
            self.diagnostics["candidate21_fresh_submissions"] += 1
        else:
            self.armed_entry_path = None
            if self.scenario_states.get(setup.scenario_id) != "CLOSED":
                self._transition(setup.scenario_id, "FRESH_SUBMISSION_DECLINED", int(row["ts"]),
                                 int(row["ts"]), "CLOSED", "SLOT_OR_EXECUTION_DECLINED",
                                 observed, armed.details)
        return True


class FreshInitiativeAcceptanceStrategy(FreshInitiativeAcceptanceMixin, ScenarioValidEntryStrategy):
    pass

CandidateStrategy = FreshInitiativeAcceptanceStrategy
StrategyClass = FreshInitiativeAcceptanceStrategy
SystemicRepricingGateMixin = FreshInitiativeAcceptanceMixin
SystemicRepricingGateStrategy = FreshInitiativeAcceptanceStrategy

__all__ = ["FreshInitiativeAcceptanceMixin", "FreshInitiativeAcceptanceStrategy",
           "SystemicRepricingGateMixin", "SystemicRepricingGateStrategy"]
