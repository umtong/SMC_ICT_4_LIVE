"""Perpetual valuation-dislocation reversion state machine.

The exchange-reported USD-M open-interest value divided by open interest is used
as a contemporaneous derivatives valuation anchor. Direction comes from the sign
of the traded-perpetual deviation from that anchor, never from OI sign. A trade
is emitted only after a tail dislocation contracts and opposite aggressor flow
confirms reversion. NautilusTrader owns execution and accounting.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from statistics import median
from typing import Any, Mapping

from model import Direction, ScenarioKind, TradePlan, Transition
from model_positioning import InventoryState, PositioningSignalBar
from model_positioning_gap_safe import GapSafePositioningAuctionRouter


class DislocationState(str, Enum):
    IDLE = "IDLE"
    DISLOCATION = "DISLOCATION"
    CONFIRMED = "CONFIRMED"
    ENTRY_READY = "ENTRY_READY"
    INVALIDATED = "INVALIDATED"


class DislocationKind(str, Enum):
    INVENTORY_BUILD = "INVENTORY_BUILD"
    INVENTORY_RELEASE = "INVENTORY_RELEASE"
    OI_ABLATION = "OI_ABLATION"


@dataclass(frozen=True, slots=True)
class ValuationLogicConfig:
    signal_minutes: int = 5
    atr_period: int = 24
    flow_period: int = 36
    oi_period: int = 36
    basis_period: int = 288
    target_lookback: int = 288
    target_pivot_radius: int = 2
    min_history: int = 288
    basis_tail_rank: float = 0.90
    basis_normal_rank: float = 0.55
    minimum_abs_basis_bps: float = 0.50
    aggression_min_imbalance: float = 0.05
    flow_impulse_z: float = 0.10
    oi_impulse_rank: float = 0.50
    contraction_fraction: float = 0.35
    confirmation_bars: int = 6
    confirmation_body_atr: float = 0.08
    confirmation_min_imbalance: float = 0.01
    stop_buffer_atr: float = 0.08
    minimum_rr: float = 1.25
    maximum_target_rr: float = 3.0
    rearm_bars: int = 3
    use_open_interest: bool = True

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "ValuationLogicConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown valuation logic config keys: {unknown}")
        config = cls(**dict(values))
        config.validate()
        return config

    def validate(self) -> None:
        for name in (
            "signal_minutes",
            "atr_period",
            "flow_period",
            "oi_period",
            "basis_period",
            "target_lookback",
            "target_pivot_radius",
            "min_history",
            "confirmation_bars",
            "rearm_bars",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_history < max(
            self.atr_period,
            self.flow_period + 1,
            self.oi_period + 1,
            self.basis_period,
            self.target_lookback,
        ):
            raise ValueError("min_history must cover all causal lookbacks")
        if self.target_lookback <= 2 * self.target_pivot_radius:
            raise ValueError("target lookback is too short")
        for name in (
            "basis_tail_rank",
            "basis_normal_rank",
            "contraction_fraction",
            "oi_impulse_rank",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.basis_tail_rank <= self.basis_normal_rank:
            raise ValueError("tail rank must exceed normalization rank")
        if self.minimum_abs_basis_bps < 0.0:
            raise ValueError("minimum_abs_basis_bps must be non-negative")
        if not 0.0 < self.aggression_min_imbalance < 1.0:
            raise ValueError("aggression_min_imbalance must be in (0, 1)")
        if not 0.0 <= self.confirmation_min_imbalance < 1.0:
            raise ValueError("confirmation_min_imbalance must be in [0, 1)")
        if self.stop_buffer_atr < 0.0:
            raise ValueError("stop buffer must be non-negative")
        if self.minimum_rr <= 0.0 or self.maximum_target_rr < self.minimum_rr:
            raise ValueError("target R parameters are inconsistent")


@dataclass(slots=True)
class _DislocationEpisode:
    scenario_id: str
    created_index: int
    created_ns: int
    sign: int
    direction: Direction
    kind: DislocationKind
    initial_basis: float
    extreme_basis: float
    initial_price: float
    extreme_price: float
    initial_anchor: float
    initial_oi_change: float
    initial_oi_rank: float
    initial_flow_z: float
    state: DislocationState = DislocationState.DISLOCATION


@dataclass(frozen=True, slots=True)
class ValuationObservation:
    plan: TradePlan | None
    transitions: tuple[Transition, ...]
    diagnostics: Mapping[str, Any]


class ValuationDislocationRouter(GapSafePositioningAuctionRouter):
    """Tail trade/valuation deviation -> contraction -> mean-reversion plan."""

    def __init__(self, config: ValuationLogicConfig):
        super().__init__(config)
        self._episode: _DislocationEpisode | None = None
        self._episode_counter = 0
        self._rearm_after_index = -1
        self._needs_normalization = True
        self._episode_count = 0

    @property
    def active_scenario_id(self) -> str | None:
        return self._episode.scenario_id if self._episode else None

    @property
    def consumed_pool_count(self) -> int:
        return self._episode_count

    @staticmethod
    def valuation_price(bar: PositioningSignalBar) -> float:
        if bar.open_interest <= 0.0 or bar.open_interest_value <= 0.0:
            raise ValueError("open-interest valuation inputs must be positive")
        return bar.open_interest_value / bar.open_interest

    @classmethod
    def basis(cls, bar: PositioningSignalBar) -> float:
        anchor = cls.valuation_price(bar)
        return (bar.close - anchor) / anchor

    def observe(
        self,
        bar: PositioningSignalBar,
        index: int,
        *,
        eligible: bool = True,
    ) -> ValuationObservation:
        if bar.ts_event_ns <= self._last_ts:
            raise ValueError("signal bars must be strictly monotonic")
        self._last_ts = bar.ts_event_ns
        transitions: list[Transition] = []
        diagnostics: dict[str, Any] = {
            "index": index,
            "history": len(self._history),
            "eligible": eligible,
        }
        if len(self._history) < self.config.min_history:
            self._history.append(bar)
            diagnostics["reason"] = "WARMUP"
            return ValuationObservation(None, tuple(), diagnostics)

        atr = self._atr()
        anchor = self.valuation_price(bar)
        current_basis = (bar.close - anchor) / anchor
        basis_rank, median_basis = self._basis_stats(current_basis)
        flow_z = self._flow_z(abs(bar.delta))
        oi_change, oi_rank, inventory_state = self._inventory_state(bar)
        diagnostics.update(
            {
                "atr": atr,
                "valuation_price": anchor,
                "basis": current_basis,
                "basis_bps": current_basis * 10_000.0,
                "absolute_basis_rank": basis_rank,
                "median_basis": median_basis,
                "median_basis_bps": median_basis * 10_000.0,
                "aggressor_imbalance": bar.imbalance,
                "aggressor_flow_z": flow_z,
                "oi_change_fraction": oi_change,
                "oi_impulse_rank": oi_rank,
                "inventory_state": inventory_state.value,
                "needs_normalization": self._needs_normalization,
                "use_open_interest": self.config.use_open_interest,
            }
        )

        if not eligible:
            self._needs_normalization = True
            if self._episode:
                transitions.append(
                    self._terminal(
                        self._episode,
                        DislocationState.INVALIDATED,
                        "ELIGIBILITY_LOST",
                        bar,
                        bar.close,
                        {},
                    )
                )
                self._finish(index)
            self._history.append(bar)
            diagnostics["reason"] = "INELIGIBLE"
            return ValuationObservation(None, tuple(transitions), diagnostics)

        if self._episode is None and basis_rank <= self.config.basis_normal_rank:
            self._needs_normalization = False

        plan: TradePlan | None = None
        if self._episode is not None:
            plan, advanced = self._advance(
                bar=bar,
                index=index,
                atr=atr,
                anchor=anchor,
                current_basis=current_basis,
                basis_rank=basis_rank,
                median_basis=median_basis,
                inventory_state=inventory_state,
                oi_change=oi_change,
                oi_rank=oi_rank,
            )
            transitions.extend(advanced)
        elif index >= self._rearm_after_index and not self._needs_normalization:
            episode, transition = self._detect(
                bar=bar,
                index=index,
                anchor=anchor,
                current_basis=current_basis,
                basis_rank=basis_rank,
                flow_z=flow_z,
                inventory_state=inventory_state,
                oi_change=oi_change,
                oi_rank=oi_rank,
            )
            if episode is not None and transition is not None:
                self._episode = episode
                self._episode_count += 1
                transitions.append(transition)

        self._history.append(bar)
        diagnostics["active_scenario_id"] = self.active_scenario_id
        return ValuationObservation(plan, tuple(transitions), diagnostics)

    def invalidate_data_gap(
        self,
        *,
        index: int,
        event_time_ns: int,
        reference_price: float,
        reason_code: str,
    ) -> tuple[Transition, ...]:
        if index < 0 or event_time_ns < 0 or reference_price <= 0.0:
            raise ValueError("gap invalidation arguments are inconsistent")
        self._needs_normalization = True
        self._rearm_after_index = max(
            self._rearm_after_index,
            index + self.config.rearm_bars,
        )
        if self._episode is None:
            return tuple()
        episode = self._episode
        transition = Transition(
            scenario_id=episode.scenario_id,
            event_type="VALUATION_DISLOCATION_TRANSITION",
            previous_state=episode.state.value,
            next_state=DislocationState.INVALIDATED.value,
            reason_code=reason_code,
            event_time_ns=event_time_ns,
            reference_price=reference_price,
            details={
                "dislocation_kind": episode.kind.value,
                "data_gap": True,
                "synthetic_positioning_used": False,
                "forward_fill_used": False,
                "interpolation_used": False,
            },
        )
        self._episode = None
        return (transition,)

    def _basis_stats(self, current_basis: float) -> tuple[float, float]:
        values = [
            self.basis(item)
            for item in list(self._history)[-self.config.basis_period :]
        ]
        absolute = [abs(value) for value in values]
        magnitude = abs(current_basis)
        less = sum(value < magnitude for value in absolute)
        equal = sum(value == magnitude for value in absolute)
        rank = (less + 0.5 * equal) / len(absolute)
        return rank, float(median(values))

    def _detect(
        self,
        *,
        bar: PositioningSignalBar,
        index: int,
        anchor: float,
        current_basis: float,
        basis_rank: float,
        flow_z: float,
        inventory_state: InventoryState,
        oi_change: float,
        oi_rank: float,
    ) -> tuple[_DislocationEpisode | None, Transition | None]:
        sign = 1 if current_basis > 0.0 else -1 if current_basis < 0.0 else 0
        minimum = self.config.minimum_abs_basis_bps / 10_000.0
        flow_aligned = (
            bar.imbalance >= self.config.aggression_min_imbalance
            if sign > 0
            else bar.imbalance <= -self.config.aggression_min_imbalance
        )
        qualified = (
            sign != 0
            and abs(current_basis) >= minimum
            and basis_rank >= self.config.basis_tail_rank
            and flow_aligned
            and flow_z >= self.config.flow_impulse_z
            and (
                not self.config.use_open_interest
                or inventory_state is not InventoryState.NEUTRAL
            )
        )
        if not qualified:
            return None, None

        kind = (
            DislocationKind.OI_ABLATION
            if not self.config.use_open_interest
            else DislocationKind.INVENTORY_BUILD
            if inventory_state is InventoryState.BUILD
            else DislocationKind.INVENTORY_RELEASE
        )
        self._episode_counter += 1
        scenario_id = f"c07v-{bar.ts_event_ns}-{self._episode_counter:06d}"
        episode = _DislocationEpisode(
            scenario_id=scenario_id,
            created_index=index,
            created_ns=bar.ts_event_ns,
            sign=sign,
            direction=Direction.SHORT if sign > 0 else Direction.LONG,
            kind=kind,
            initial_basis=current_basis,
            extreme_basis=current_basis,
            initial_price=bar.close,
            extreme_price=bar.high if sign > 0 else bar.low,
            initial_anchor=anchor,
            initial_oi_change=oi_change,
            initial_oi_rank=oi_rank,
            initial_flow_z=flow_z,
        )
        transition = Transition(
            scenario_id=scenario_id,
            event_type="VALUATION_DISLOCATION_TRANSITION",
            previous_state=DislocationState.IDLE.value,
            next_state=DislocationState.DISLOCATION.value,
            reason_code="PERPETUAL_VALUATION_TAIL_DISLOCATION",
            event_time_ns=bar.ts_event_ns,
            reference_price=anchor,
            details={
                "direction": episode.direction.value,
                "dislocation_kind": kind.value,
                "basis": current_basis,
                "basis_bps": current_basis * 10_000.0,
                "absolute_basis_rank": basis_rank,
                "trade_price": bar.close,
                "valuation_price": anchor,
                "aggressor_imbalance": bar.imbalance,
                "aggressor_flow_z": flow_z,
                "inventory_state": inventory_state.value,
                "oi_change_fraction": oi_change,
                "oi_impulse_rank": oi_rank,
            },
        )
        return episode, transition

    def _advance(
        self,
        *,
        bar: PositioningSignalBar,
        index: int,
        atr: float,
        anchor: float,
        current_basis: float,
        basis_rank: float,
        median_basis: float,
        inventory_state: InventoryState,
        oi_change: float,
        oi_rank: float,
    ) -> tuple[TradePlan | None, list[Transition]]:
        episode = self._episode
        if episode is None:
            return None, []
        age = index - episode.created_index
        if age > self.config.confirmation_bars:
            transition = self._terminal(
                episode,
                DislocationState.INVALIDATED,
                "DISLOCATION_CONTRACTION_TIMEOUT",
                bar,
                bar.close,
                {
                    "age_bars": age,
                    "current_basis_bps": current_basis * 10_000.0,
                    "absolute_basis_rank": basis_rank,
                },
            )
            self._finish(index)
            return None, [transition]

        same_sign = current_basis * episode.sign > 0.0
        if same_sign and abs(current_basis) > abs(episode.extreme_basis):
            episode.extreme_basis = current_basis
            episode.extreme_price = (
                max(episode.extreme_price, bar.high)
                if episode.sign > 0
                else min(episode.extreme_price, bar.low)
            )
        contraction = (
            1.0 - abs(current_basis) / abs(episode.extreme_basis)
            if same_sign
            else 1.0 + abs(current_basis) / abs(episode.extreme_basis)
        )
        body_ok = bar.body >= self.config.confirmation_body_atr * atr
        if episode.sign > 0:
            opposite_flow = (
                bar.imbalance <= -self.config.confirmation_min_imbalance
                and bar.close < bar.open
            )
            price_reversed = bar.close < episode.initial_price
        else:
            opposite_flow = (
                bar.imbalance >= self.config.confirmation_min_imbalance
                and bar.close > bar.open
            )
            price_reversed = bar.close > episode.initial_price
        confirmed = (
            contraction >= self.config.contraction_fraction
            and body_ok
            and opposite_flow
            and price_reversed
        )
        if not confirmed:
            if basis_rank <= self.config.basis_normal_rank and contraction > 0.0:
                transition = self._terminal(
                    episode,
                    DislocationState.INVALIDATED,
                    "DISLOCATION_NORMALIZED_WITHOUT_OPPOSITE_FLOW",
                    bar,
                    anchor,
                    {
                        "age_bars": age,
                        "contraction_fraction": contraction,
                        "current_basis_bps": current_basis * 10_000.0,
                    },
                )
                self._finish(index)
                return None, [transition]
            return None, []

        previous = episode.state
        episode.state = DislocationState.CONFIRMED
        transitions = [
            Transition(
                scenario_id=episode.scenario_id,
                event_type="VALUATION_DISLOCATION_TRANSITION",
                previous_state=previous.value,
                next_state=DislocationState.CONFIRMED.value,
                reason_code="VALUATION_BASIS_CONTRACTED_WITH_COUNTERFLOW",
                event_time_ns=bar.ts_event_ns,
                reference_price=anchor,
                details={
                    "direction": episode.direction.value,
                    "dislocation_kind": episode.kind.value,
                    "age_bars": age,
                    "extreme_basis_bps": episode.extreme_basis * 10_000.0,
                    "current_basis_bps": current_basis * 10_000.0,
                    "contraction_fraction": contraction,
                    "median_basis_bps": median_basis * 10_000.0,
                    "inventory_state": inventory_state.value,
                    "oi_change_fraction": oi_change,
                    "oi_impulse_rank": oi_rank,
                    "confirmation_imbalance": bar.imbalance,
                },
            )
        ]
        plan = self._build_plan(
            episode=episode,
            bar=bar,
            atr=atr,
            anchor=anchor,
            median_basis=median_basis,
            age=age,
            contraction=contraction,
        )
        if plan is None:
            transitions.append(
                self._terminal(
                    episode,
                    DislocationState.INVALIDATED,
                    "UNTRADEABLE_VALUATION_GEOMETRY",
                    bar,
                    bar.close,
                    {
                        "age_bars": age,
                        "contraction_fraction": contraction,
                        "valuation_price": anchor,
                        "median_basis": median_basis,
                    },
                )
            )
            self._finish(index)
            return None, transitions

        previous = episode.state
        episode.state = DislocationState.ENTRY_READY
        transitions.append(
            Transition(
                scenario_id=episode.scenario_id,
                event_type="VALUATION_DISLOCATION_TRANSITION",
                previous_state=previous.value,
                next_state=DislocationState.ENTRY_READY.value,
                reason_code="VALUATION_REVERSION_ROUTE_READY",
                event_time_ns=bar.ts_event_ns,
                reference_price=plan.entry_reference,
                details={
                    "direction": plan.direction.value,
                    "dislocation_kind": episode.kind.value,
                    "stop": plan.stop_price,
                    "target": plan.target_price,
                    "expected_rr": plan.expected_rr,
                },
            )
        )
        self._finish(index)
        return plan, transitions

    def _build_plan(
        self,
        *,
        episode: _DislocationEpisode,
        bar: PositioningSignalBar,
        atr: float,
        anchor: float,
        median_basis: float,
        age: int,
        contraction: float,
    ) -> TradePlan | None:
        entry = bar.close
        fair_value = anchor * (1.0 + median_basis)
        buffer = self.config.stop_buffer_atr * atr
        if episode.direction is Direction.SHORT:
            stop = bar.high + buffer
            risk = stop - entry
            reward = entry - fair_value
        else:
            stop = bar.low - buffer
            risk = entry - stop
            reward = fair_value - entry
        if risk <= 0.0 or reward <= 0.0:
            return None
        uncapped_rr = reward / risk
        if uncapped_rr < self.config.minimum_rr:
            return None
        target_rr = min(uncapped_rr, self.config.maximum_target_rr)
        target = (
            entry - risk * target_rr
            if episode.direction is Direction.SHORT
            else entry + risk * target_rr
        )
        return TradePlan(
            scenario_id=episode.scenario_id,
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=episode.direction,
            observed_time_ns=bar.ts_event_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=anchor,
            expected_rr=target_rr,
            details={
                "atr": atr,
                "route_age_bars": age,
                "dislocation_kind": episode.kind.value,
                "initial_basis": episode.initial_basis,
                "extreme_basis": episode.extreme_basis,
                "contraction_fraction": contraction,
                "initial_price": episode.initial_price,
                "extreme_price": episode.extreme_price,
                "initial_valuation_price": episode.initial_anchor,
                "confirmation_valuation_price": anchor,
                "median_basis": median_basis,
                "fair_value_target": fair_value,
                "uncapped_target_rr": uncapped_rr,
                "initial_oi_change_fraction": episode.initial_oi_change,
                "initial_oi_impulse_rank": episode.initial_oi_rank,
                "initial_flow_z": episode.initial_flow_z,
                "use_open_interest": self.config.use_open_interest,
            },
        )

    def _terminal(
        self,
        episode: _DislocationEpisode,
        next_state: DislocationState,
        reason: str,
        bar: PositioningSignalBar,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> Transition:
        previous = episode.state
        episode.state = next_state
        return Transition(
            scenario_id=episode.scenario_id,
            event_type="VALUATION_DISLOCATION_TRANSITION",
            previous_state=previous.value,
            next_state=next_state.value,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=reference_price,
            details={
                "dislocation_kind": episode.kind.value,
                **dict(details),
            },
        )

    def _finish(self, index: int) -> None:
        self._episode = None
        self._needs_normalization = True
        self._rearm_after_index = max(
            self._rearm_after_index,
            index + self.config.rearm_bars,
        )


__all__ = [
    "DislocationKind",
    "DislocationState",
    "ValuationDislocationRouter",
    "ValuationLogicConfig",
    "ValuationObservation",
]
