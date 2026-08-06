"""Balance-to-initiative auction state machine for candidate-07.

A completed rotational balance is frozen before a new bar is evaluated. A
tradable initiative requires aligned aggressor flow and new open-interest build.
The next completed bars either hold outside the balance or return inside while
that newly built inventory releases. The module emits plans only; NautilusTrader
owns orders, fills, cash, positions, fees, funding and NAV.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from model import Direction, ScenarioKind, TradePlan, Transition
from model_positioning import InventoryState, PositioningSignalBar
from model_positioning_gap_safe import GapSafePositioningAuctionRouter


class InitiativeBranch(str, Enum):
    ACCEPTED_INITIATIVE = "ACCEPTED_INITIATIVE"
    FAILED_INITIATIVE = "FAILED_INITIATIVE"
    INITIATIVE_ABLATION = "INITIATIVE_ABLATION"


class BalanceState(str, Enum):
    IDLE = "IDLE"
    BALANCE_LOCKED = "BALANCE_LOCKED"
    INITIATIVE_BREAK = "INITIATIVE_BREAK"
    CONFIRMED = "CONFIRMED"
    ENTRY_READY = "ENTRY_READY"
    INVALIDATED = "INVALIDATED"


@dataclass(frozen=True, slots=True)
class BalanceLogicConfig:
    signal_minutes: int = 5
    atr_period: int = 24
    flow_period: int = 36
    oi_period: int = 36
    balance_bars: int = 12
    target_lookback: int = 288
    target_pivot_radius: int = 2
    min_history: int = 288
    balance_max_width_atr: float = 4.0
    balance_max_efficiency: float = 0.35
    balance_boundary_tolerance_atr: float = 0.25
    balance_min_touches_each_side: int = 2
    balance_min_oi_change: float = 0.0
    break_buffer_atr: float = 0.05
    break_body_atr: float = 0.25
    break_body_fraction: float = 0.55
    break_close_location: float = 0.70
    aggression_min_imbalance: float = 0.08
    flow_impulse_z: float = 0.25
    oi_impulse_rank: float = 0.50
    confirmation_bars: int = 3
    confirmation_body_atr: float = 0.12
    confirmation_min_imbalance: float = 0.02
    hold_buffer_atr: float = 0.02
    failure_reentry_atr: float = 0.02
    stop_buffer_atr: float = 0.10
    minimum_rr: float = 1.25
    maximum_target_rr: float = 3.0
    rearm_bars: int = 12
    use_open_interest: bool = True

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "BalanceLogicConfig":
        unknown = sorted(set(values) - set(cls.__dataclass_fields__))
        if unknown:
            raise ValueError(f"unknown balance logic config keys: {unknown}")
        config = cls(**dict(values))
        config.validate()
        return config

    def validate(self) -> None:
        for name in (
            "signal_minutes",
            "atr_period",
            "flow_period",
            "oi_period",
            "balance_bars",
            "target_lookback",
            "target_pivot_radius",
            "min_history",
            "balance_min_touches_each_side",
            "confirmation_bars",
            "rearm_bars",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.min_history < max(
            self.atr_period,
            self.flow_period + 1,
            self.oi_period + 1,
            self.balance_bars,
            self.target_lookback,
        ):
            raise ValueError("min_history must cover all causal lookbacks")
        if self.target_lookback <= self.balance_bars:
            raise ValueError("target_lookback must exceed balance_bars")
        if self.target_lookback <= 2 * self.target_pivot_radius:
            raise ValueError("target_lookback is too short for pivot radius")
        if self.balance_min_touches_each_side > self.balance_bars:
            raise ValueError("touch requirement exceeds balance length")
        for name in (
            "balance_max_efficiency",
            "break_body_fraction",
            "break_close_location",
            "oi_impulse_rank",
        ):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.break_close_location < 0.5:
            raise ValueError("break_close_location must be at least 0.5")
        if self.balance_max_width_atr <= 0.0:
            raise ValueError("balance_max_width_atr must be positive")
        if self.balance_boundary_tolerance_atr < 0.0:
            raise ValueError("balance boundary tolerance must be non-negative")
        if min(
            self.break_buffer_atr,
            self.stop_buffer_atr,
            self.hold_buffer_atr,
            self.failure_reentry_atr,
        ) < 0.0:
            raise ValueError("buffers must be non-negative")
        if not 0.0 < self.aggression_min_imbalance < 1.0:
            raise ValueError("aggression_min_imbalance must be in (0, 1)")
        if not 0.0 <= self.confirmation_min_imbalance < 1.0:
            raise ValueError("confirmation_min_imbalance must be in [0, 1)")
        if self.minimum_rr <= 0.0 or self.maximum_target_rr < self.minimum_rr:
            raise ValueError("target R parameters are inconsistent")


@dataclass(slots=True)
class _BalanceEpisode:
    scenario_id: str
    balance_start_ns: int
    balance_end_ns: int
    upper: float
    lower: float
    midpoint: float
    width: float
    atr: float
    upper_formed_ns: int
    lower_formed_ns: int
    balance_oi_change: float
    state: BalanceState = BalanceState.BALANCE_LOCKED
    branch: InitiativeBranch | None = None
    direction: Direction | None = None
    break_index: int | None = None
    break_time_ns: int | None = None
    break_extreme: float | None = None
    break_close: float | None = None
    break_oi_change: float = 0.0
    break_oi_rank: float = 0.0
    break_flow_z: float = 0.0


@dataclass(frozen=True, slots=True)
class BalanceObservation:
    plan: TradePlan | None
    transitions: tuple[Transition, ...]
    diagnostics: Mapping[str, Any]


class BalanceInitiativeRouter(GapSafePositioningAuctionRouter):
    """Frozen balance -> OI-backed initiative -> acceptance or trapped unwind."""

    def __init__(self, config: BalanceLogicConfig):
        super().__init__(config)
        self._episode: _BalanceEpisode | None = None
        self._episode_counter = 0
        self._rearm_after_index = -1
        self._consumed_balances: set[tuple[int, int, float, float]] = set()

    @property
    def active_scenario_id(self) -> str | None:
        return self._episode.scenario_id if self._episode is not None else None

    @property
    def consumed_pool_count(self) -> int:
        return len(self._consumed_balances)

    def observe(
        self,
        bar: PositioningSignalBar,
        index: int,
        *,
        eligible: bool = True,
    ) -> BalanceObservation:
        if bar.ts_event_ns <= self._last_ts:
            raise ValueError("signal bars must be strictly monotonic")
        self._last_ts = bar.ts_event_ns
        transitions: list[Transition] = []
        diagnostics: dict[str, Any] = {
            "index": index,
            "history": len(self._history),
            "eligible": eligible,
            "imbalance": bar.imbalance,
            "open_interest": bar.open_interest,
        }
        if len(self._history) < self.config.min_history:
            self._history.append(bar)
            diagnostics["reason"] = "WARMUP"
            return BalanceObservation(None, tuple(), diagnostics)

        atr = self._atr()
        flow_z = self._flow_z(abs(bar.delta))
        oi_change, oi_rank, inventory_state = self._inventory_state(bar)
        diagnostics.update(
            {
                "atr": atr,
                "aggressor_flow_z": flow_z,
                "oi_change_fraction": oi_change,
                "oi_impulse_rank": oi_rank,
                "inventory_state": inventory_state.value,
                "use_open_interest": self.config.use_open_interest,
                "rearm_after_index": self._rearm_after_index,
                "consumed_balance_count": self.consumed_pool_count,
            }
        )

        if not eligible:
            if self._episode is not None:
                transitions.append(
                    self._terminal(
                        self._episode,
                        BalanceState.INVALIDATED,
                        "ELIGIBILITY_LOST",
                        bar,
                        bar.close,
                        {},
                    )
                )
                self._finish(index)
            self._history.append(bar)
            diagnostics["reason"] = "INELIGIBLE"
            return BalanceObservation(None, tuple(transitions), diagnostics)

        plan: TradePlan | None = None
        if self._episode is None and index >= self._rearm_after_index:
            episode, transition, balance_diagnostics = self._lock_balance(atr)
            diagnostics.update(balance_diagnostics)
            if episode is not None and transition is not None:
                self._episode = episode
                transitions.append(transition)

        if self._episode is not None:
            plan, advanced = self._advance(
                bar,
                index,
                atr,
                flow_z,
                oi_change,
                oi_rank,
                inventory_state,
            )
            transitions.extend(advanced)

        self._history.append(bar)
        diagnostics["active_scenario_id"] = self.active_scenario_id
        return BalanceObservation(plan, tuple(transitions), diagnostics)

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
        self._rearm_after_index = max(
            self._rearm_after_index,
            index + self.config.rearm_bars,
        )
        episode = self._episode
        if episode is None:
            return tuple()
        transition = Transition(
            scenario_id=episode.scenario_id,
            event_type="BALANCE_AUCTION_TRANSITION",
            previous_state=episode.state.value,
            next_state=BalanceState.INVALIDATED.value,
            reason_code=reason_code,
            event_time_ns=event_time_ns,
            reference_price=reference_price,
            details={
                "branch": episode.branch.value if episode.branch else None,
                "data_gap": True,
                "synthetic_positioning_used": False,
                "forward_fill_used": False,
                "interpolation_used": False,
            },
        )
        self._episode = None
        return (transition,)

    def _lock_balance(
        self,
        atr: float,
    ) -> tuple[_BalanceEpisode | None, Transition | None, dict[str, Any]]:
        window = list(self._history)[-self.config.balance_bars :]
        upper_bar = max(window, key=lambda item: item.high)
        lower_bar = min(window, key=lambda item: item.low)
        upper, lower = upper_bar.high, lower_bar.low
        width = upper - lower
        closes = [item.close for item in window]
        path = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        efficiency = abs(closes[-1] - closes[0]) / path if path > 0.0 else 0.0
        width_atr = width / atr if atr > 0.0 else float("inf")
        tolerance = self.config.balance_boundary_tolerance_atr * atr
        upper_touches = sum(item.high >= upper - tolerance for item in window)
        lower_touches = sum(item.low <= lower + tolerance for item in window)
        oi_change = (
            (window[-1].open_interest - window[0].open_interest)
            / window[0].open_interest
        )
        diagnostics = {
            "balance_width": width,
            "balance_width_atr": width_atr,
            "balance_efficiency": efficiency,
            "balance_upper_touches": upper_touches,
            "balance_lower_touches": lower_touches,
            "balance_oi_change_fraction": oi_change,
        }
        qualified = (
            width > 0.0
            and width_atr <= self.config.balance_max_width_atr
            and efficiency <= self.config.balance_max_efficiency
            and upper_touches >= self.config.balance_min_touches_each_side
            and lower_touches >= self.config.balance_min_touches_each_side
            and (
                not self.config.use_open_interest
                or oi_change >= self.config.balance_min_oi_change
            )
        )
        if not qualified:
            diagnostics["balance_reason"] = "BALANCE_NOT_QUALIFIED"
            return None, None, diagnostics

        key = (
            window[0].ts_event_ns,
            window[-1].ts_event_ns,
            round(upper, 8),
            round(lower, 8),
        )
        if key in self._consumed_balances:
            diagnostics["balance_reason"] = "BALANCE_ALREADY_CONSUMED"
            return None, None, diagnostics
        self._consumed_balances.add(key)
        self._episode_counter += 1
        scenario_id = f"c07b-{window[-1].ts_event_ns}-{self._episode_counter:06d}"
        episode = _BalanceEpisode(
            scenario_id=scenario_id,
            balance_start_ns=window[0].ts_event_ns,
            balance_end_ns=window[-1].ts_event_ns,
            upper=upper,
            lower=lower,
            midpoint=(upper + lower) / 2.0,
            width=width,
            atr=atr,
            upper_formed_ns=upper_bar.ts_event_ns,
            lower_formed_ns=lower_bar.ts_event_ns,
            balance_oi_change=oi_change,
        )
        diagnostics["balance_reason"] = "BALANCE_LOCKED"
        transition = Transition(
            scenario_id=scenario_id,
            event_type="BALANCE_AUCTION_TRANSITION",
            previous_state=BalanceState.IDLE.value,
            next_state=BalanceState.BALANCE_LOCKED.value,
            reason_code="ROTATIONAL_BALANCE_FROZEN",
            event_time_ns=window[-1].ts_event_ns,
            reference_price=episode.midpoint,
            details={
                "balance_start_ns": episode.balance_start_ns,
                "balance_end_ns": episode.balance_end_ns,
                "upper": upper,
                "lower": lower,
                "midpoint": episode.midpoint,
                "width": width,
                "width_atr": width_atr,
                "path_efficiency": efficiency,
                "upper_touches": upper_touches,
                "lower_touches": lower_touches,
                "balance_oi_change_fraction": oi_change,
                "upper_formed_ns": episode.upper_formed_ns,
                "lower_formed_ns": episode.lower_formed_ns,
            },
        )
        return episode, transition, diagnostics

    def _advance(
        self,
        bar: PositioningSignalBar,
        index: int,
        atr: float,
        flow_z: float,
        oi_change: float,
        oi_rank: float,
        inventory_state: InventoryState,
    ) -> tuple[TradePlan | None, list[Transition]]:
        episode = self._episode
        if episode is None:
            return None, []
        if episode.state is BalanceState.BALANCE_LOCKED:
            return self._detect_break(
                episode,
                bar,
                index,
                atr,
                flow_z,
                oi_change,
                oi_rank,
                inventory_state,
            )
        if episode.state is BalanceState.INITIATIVE_BREAK:
            return self._confirm(
                episode,
                bar,
                index,
                atr,
                oi_change,
                oi_rank,
                inventory_state,
            )
        raise RuntimeError(f"unexpected active balance state: {episode.state}")

    def _detect_break(
        self,
        episode: _BalanceEpisode,
        bar: PositioningSignalBar,
        index: int,
        atr: float,
        flow_z: float,
        oi_change: float,
        oi_rank: float,
        inventory_state: InventoryState,
    ) -> tuple[TradePlan | None, list[Transition]]:
        buffer = self.config.break_buffer_atr * atr
        body_fraction = bar.body / bar.range if bar.range > 0.0 else 0.0
        body_ok = (
            bar.body >= self.config.break_body_atr * atr
            and body_fraction >= self.config.break_body_fraction
        )
        buy_flow = (
            bar.imbalance >= self.config.aggression_min_imbalance
            and flow_z >= self.config.flow_impulse_z
        )
        sell_flow = (
            bar.imbalance <= -self.config.aggression_min_imbalance
            and flow_z >= self.config.flow_impulse_z
        )
        long_break = (
            body_ok
            and buy_flow
            and bar.close > episode.upper + buffer
            and bar.close_location >= self.config.break_close_location
        )
        short_break = (
            body_ok
            and sell_flow
            and bar.close < episode.lower - buffer
            and bar.close_location <= 1.0 - self.config.break_close_location
        )
        if not long_break and not short_break:
            if bar.close > episode.upper + buffer or bar.close < episode.lower - buffer:
                transition = self._terminal(
                    episode,
                    BalanceState.INVALIDATED,
                    "BALANCE_BROKEN_WITHOUT_INITIATIVE",
                    bar,
                    bar.close,
                    {
                        "inventory_state": inventory_state.value,
                        "oi_change_fraction": oi_change,
                        "oi_impulse_rank": oi_rank,
                        "aggressor_flow_z": flow_z,
                        "body_fraction": body_fraction,
                    },
                )
                self._finish(index)
                return None, [transition]
            return None, []

        if self.config.use_open_interest and inventory_state is not InventoryState.BUILD:
            transition = self._terminal(
                episode,
                BalanceState.INVALIDATED,
                "BREAK_WITHOUT_NEW_INVENTORY",
                bar,
                bar.close,
                {
                    "inventory_state": inventory_state.value,
                    "oi_change_fraction": oi_change,
                    "oi_impulse_rank": oi_rank,
                    "aggressor_flow_z": flow_z,
                },
            )
            self._finish(index)
            return None, [transition]

        episode.direction = Direction.LONG if long_break else Direction.SHORT
        episode.branch = (
            InitiativeBranch.ACCEPTED_INITIATIVE
            if self.config.use_open_interest
            else InitiativeBranch.INITIATIVE_ABLATION
        )
        episode.break_index = index
        episode.break_time_ns = bar.ts_event_ns
        episode.break_extreme = bar.high if long_break else bar.low
        episode.break_close = bar.close
        episode.break_oi_change = oi_change
        episode.break_oi_rank = oi_rank
        episode.break_flow_z = flow_z
        previous = episode.state
        episode.state = BalanceState.INITIATIVE_BREAK
        transition = Transition(
            scenario_id=episode.scenario_id,
            event_type="BALANCE_AUCTION_TRANSITION",
            previous_state=previous.value,
            next_state=BalanceState.INITIATIVE_BREAK.value,
            reason_code="NEW_INVENTORY_INITIATIVE_BREAK",
            event_time_ns=bar.ts_event_ns,
            reference_price=episode.upper if long_break else episode.lower,
            details={
                "direction": episode.direction.value,
                "branch": episode.branch.value,
                "balance_upper": episode.upper,
                "balance_lower": episode.lower,
                "balance_midpoint": episode.midpoint,
                "break_close": bar.close,
                "break_extreme": episode.break_extreme,
                "body_atr": bar.body / atr,
                "body_fraction": body_fraction,
                "close_location": bar.close_location,
                "aggressor_imbalance": bar.imbalance,
                "aggressor_flow_z": flow_z,
                "inventory_state": inventory_state.value,
                "oi_change_fraction": oi_change,
                "oi_impulse_rank": oi_rank,
            },
        )
        return None, [transition]

    def _confirm(
        self,
        episode: _BalanceEpisode,
        bar: PositioningSignalBar,
        index: int,
        atr: float,
        oi_change: float,
        oi_rank: float,
        inventory_state: InventoryState,
    ) -> tuple[TradePlan | None, list[Transition]]:
        if episode.direction is None or episode.break_index is None:
            raise RuntimeError("initiative episode missing direction or break index")
        age = index - episode.break_index
        if age > self.config.confirmation_bars:
            transition = self._terminal(
                episode,
                BalanceState.INVALIDATED,
                "INITIATIVE_CONFIRMATION_TIMEOUT",
                bar,
                bar.close,
                {"age_bars": age},
            )
            self._finish(index)
            return None, [transition]

        body_ok = bar.body >= self.config.confirmation_body_atr * atr
        if episode.direction is Direction.LONG:
            held = bar.close > episode.upper + self.config.hold_buffer_atr * episode.atr
            directional = (
                body_ok
                and bar.close > bar.open
                and bar.imbalance >= self.config.confirmation_min_imbalance
            )
            failed_inside = (
                bar.close < episode.upper - self.config.failure_reentry_atr * episode.atr
            )
            opposite_flow = (
                body_ok
                and bar.close < bar.open
                and bar.imbalance <= -self.config.confirmation_min_imbalance
            )
            midpoint_invalidated = bar.close <= episode.midpoint
        else:
            held = bar.close < episode.lower - self.config.hold_buffer_atr * episode.atr
            directional = (
                body_ok
                and bar.close < bar.open
                and bar.imbalance <= -self.config.confirmation_min_imbalance
            )
            failed_inside = (
                bar.close > episode.lower + self.config.failure_reentry_atr * episode.atr
            )
            opposite_flow = (
                body_ok
                and bar.close > bar.open
                and bar.imbalance >= self.config.confirmation_min_imbalance
            )
            midpoint_invalidated = bar.close >= episode.midpoint

        accepted = (
            held
            and directional
            and (
                not self.config.use_open_interest
                or inventory_state is not InventoryState.RELEASE
            )
        )
        failed = (
            failed_inside
            and opposite_flow
            and (
                not self.config.use_open_interest
                or inventory_state is InventoryState.RELEASE
            )
        )
        if not accepted and not failed:
            if midpoint_invalidated:
                transition = self._terminal(
                    episode,
                    BalanceState.INVALIDATED,
                    "INITIATIVE_NEGATED_WITHOUT_UNWIND",
                    bar,
                    episode.midpoint,
                    {
                        "age_bars": age,
                        "inventory_state": inventory_state.value,
                        "oi_change_fraction": oi_change,
                        "oi_impulse_rank": oi_rank,
                        "confirmation_imbalance": bar.imbalance,
                    },
                )
                self._finish(index)
                return None, [transition]
            return None, []

        if failed:
            episode.branch = InitiativeBranch.FAILED_INITIATIVE
            episode.direction = (
                Direction.SHORT
                if episode.direction is Direction.LONG
                else Direction.LONG
            )
            reason = "TRAPPED_INITIATIVE_INVENTORY_UNWOUND"
        else:
            episode.branch = (
                InitiativeBranch.ACCEPTED_INITIATIVE
                if self.config.use_open_interest
                else InitiativeBranch.INITIATIVE_ABLATION
            )
            reason = "NEW_INVENTORY_ACCEPTANCE_HELD"

        transitions: list[Transition] = []
        previous = episode.state
        episode.state = BalanceState.CONFIRMED
        transitions.append(
            Transition(
                scenario_id=episode.scenario_id,
                event_type="BALANCE_AUCTION_TRANSITION",
                previous_state=previous.value,
                next_state=BalanceState.CONFIRMED.value,
                reason_code=reason,
                event_time_ns=bar.ts_event_ns,
                reference_price=bar.close,
                details={
                    "branch": episode.branch.value,
                    "direction": episode.direction.value,
                    "age_bars": age,
                    "inventory_state": inventory_state.value,
                    "oi_change_fraction": oi_change,
                    "oi_impulse_rank": oi_rank,
                    "confirmation_imbalance": bar.imbalance,
                    "balance_upper": episode.upper,
                    "balance_lower": episode.lower,
                    "balance_midpoint": episode.midpoint,
                },
            )
        )
        plan = self._build_plan(episode, bar, atr, age)
        if plan is None:
            transitions.append(
                self._terminal(
                    episode,
                    BalanceState.INVALIDATED,
                    "UNTRADEABLE_BALANCE_GEOMETRY",
                    bar,
                    bar.close,
                    {
                        "branch": episode.branch.value,
                        "direction": episode.direction.value,
                        "age_bars": age,
                    },
                )
            )
            self._finish(index)
            return None, transitions

        previous = episode.state
        episode.state = BalanceState.ENTRY_READY
        transitions.append(
            Transition(
                scenario_id=episode.scenario_id,
                event_type="BALANCE_AUCTION_TRANSITION",
                previous_state=previous.value,
                next_state=BalanceState.ENTRY_READY.value,
                reason_code="BALANCE_AUCTION_ROUTE_READY",
                event_time_ns=bar.ts_event_ns,
                reference_price=plan.entry_reference,
                details={
                    "branch": episode.branch.value,
                    "direction": plan.direction.value,
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
        episode: _BalanceEpisode,
        bar: PositioningSignalBar,
        atr: float,
        age: int,
    ) -> TradePlan | None:
        if episode.direction is None or episode.branch is None:
            return None
        entry = bar.close
        buffer = self.config.stop_buffer_atr * atr
        failed = episode.branch is InitiativeBranch.FAILED_INITIATIVE

        if failed:
            if episode.break_extreme is None:
                return None
            if episode.direction is Direction.LONG:
                stop = episode.break_extreme - buffer
                levels = [
                    ("BALANCE_MIDPOINT", episode.midpoint),
                    ("OPPOSITE_BOUNDARY", episode.upper),
                ]
                liquidity_level = episode.lower
            else:
                stop = episode.break_extreme + buffer
                levels = [
                    ("BALANCE_MIDPOINT", episode.midpoint),
                    ("OPPOSITE_BOUNDARY", episode.lower),
                ]
                liquidity_level = episode.upper
            kind = ScenarioKind.ABSORPTION_RECLAIM
        else:
            if episode.direction is Direction.LONG:
                stop = episode.upper - buffer
                measured_move = episode.upper + episode.width
                liquidity_level = episode.upper
            else:
                stop = episode.lower + buffer
                measured_move = episode.lower - episode.width
                liquidity_level = episode.lower
            levels = [
                ("BALANCE_MEASURED_MOVE", measured_move),
                *[
                    ("DIRECTIONAL_PIVOT", level)
                    for level in self._directional_targets(episode.direction, entry)
                ],
            ]
            kind = ScenarioKind.ACCEPTANCE_CONTINUATION

        risk = entry - stop if episode.direction is Direction.LONG else stop - entry
        if risk <= 0.0:
            return None
        ordered: list[tuple[str, float, float]] = []
        seen: set[float] = set()
        for label, level in levels:
            if level in seen:
                continue
            seen.add(level)
            favorable = level > entry if episode.direction is Direction.LONG else level < entry
            if favorable:
                ordered.append((label, level, abs(level - entry) / risk))
        ordered.sort(key=lambda item: abs(item[1] - entry))
        selected = next(
            (item for item in ordered if item[2] >= self.config.minimum_rr),
            None,
        )
        if selected is None:
            return None
        label, target_level, uncapped_rr = selected
        target_rr = min(uncapped_rr, self.config.maximum_target_rr)
        target = (
            entry + risk * target_rr
            if episode.direction is Direction.LONG
            else entry - risk * target_rr
        )
        return TradePlan(
            scenario_id=episode.scenario_id,
            kind=kind,
            direction=episode.direction,
            observed_time_ns=bar.ts_event_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=liquidity_level,
            expected_rr=target_rr,
            details={
                "atr": atr,
                "route_age_bars": age,
                "branch": episode.branch.value,
                "selected_target_label": label,
                "selected_target_level": target_level,
                "uncapped_target_rr": uncapped_rr,
                "balance_start_ns": episode.balance_start_ns,
                "balance_end_ns": episode.balance_end_ns,
                "balance_upper": episode.upper,
                "balance_lower": episode.lower,
                "balance_midpoint": episode.midpoint,
                "balance_width": episode.width,
                "balance_oi_change_fraction": episode.balance_oi_change,
                "break_time_ns": episode.break_time_ns,
                "break_close": episode.break_close,
                "break_extreme": episode.break_extreme,
                "break_oi_change_fraction": episode.break_oi_change,
                "break_oi_impulse_rank": episode.break_oi_rank,
                "break_flow_z": episode.break_flow_z,
                "use_open_interest": self.config.use_open_interest,
            },
        )

    def _terminal(
        self,
        episode: _BalanceEpisode,
        next_state: BalanceState,
        reason: str,
        bar: PositioningSignalBar,
        reference_price: float,
        details: Mapping[str, Any],
    ) -> Transition:
        previous = episode.state
        episode.state = next_state
        return Transition(
            scenario_id=episode.scenario_id,
            event_type="BALANCE_AUCTION_TRANSITION",
            previous_state=previous.value,
            next_state=next_state.value,
            reason_code=reason,
            event_time_ns=bar.ts_event_ns,
            reference_price=reference_price,
            details={
                "branch": episode.branch.value if episode.branch else None,
                **dict(details),
            },
        )

    def _finish(self, index: int) -> None:
        self._episode = None
        self._rearm_after_index = max(
            self._rearm_after_index,
            index + self.config.rearm_bars,
        )


__all__ = [
    "BalanceInitiativeRouter",
    "BalanceLogicConfig",
    "BalanceObservation",
    "BalanceState",
    "InitiativeBranch",
]
