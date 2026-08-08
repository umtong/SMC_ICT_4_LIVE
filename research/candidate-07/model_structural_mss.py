"""Five-minute liquidity sweep -> protected 1M MSS -> same-boundary retest.

The original candidate called one opposite-colour reclaim candle an MSS. This
module keeps the same causal five-minute source-liquidity contact, sweep, target,
stop and risk geometry, but changes the state transition itself:

1. a completed five-minute bar sweeps and reclaims prior external liquidity;
2. the latest independently confirmed, event-local one-minute opposing swing is
   fixed as the protected boundary;
3. a later ranked one-minute displacement must close through that boundary;
4. the first subsequent touch of the exact boundary must reject on the new side;
5. only then can the existing source-extreme stop and opposing-liquidity target
   become an entry-ready TradePlan.

The detector owns market/scenario state only. It does not simulate orders,
fills, cash, positions, PnL or NAV; NautilusTrader remains the sole execution and
accounting engine.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from statistics import fmean
from typing import Any

from model import (
    CausalLiquidityRouter,
    Direction,
    LogicConfig,
    Observation,
    ScenarioKind,
    ScenarioState,
    SignalBar,
    TradePlan,
    Transition,
)
from model_impact_mss_fvg import ImpactMSSFVGLogic


NS_PER_MINUTE = 60_000_000_000


class StructuralStage(str, Enum):
    AWAIT_MSS = "AWAIT_MSS"
    AWAIT_RETEST = "AWAIT_RETEST"


@dataclass(frozen=True, slots=True)
class MinuteSwing:
    swing_id: str
    side: str
    level: float
    pivot_ns: int
    confirmed_ns: int


@dataclass(slots=True)
class _StructuralEpisode:
    scenario_id: str
    direction: Direction
    source_time_ns: int
    source_signal_index: int
    source_level: float
    event_extreme: float
    event_atr: float
    opposing_internal: float
    opposing_external: float
    boundary: MinuteSwing
    stage: StructuralStage = StructuralStage.AWAIT_MSS
    scenario_state: ScenarioState = ScenarioState.CONTACTED
    mss_ns: int | None = None
    mss_body_atr: float | None = None
    mss_rank: float | None = None


class StructuralMSSRouter(CausalLiquidityRouter):
    """Use the base source event but refuse to let it confirm itself."""

    def __init__(self, config: LogicConfig):
        super().__init__(config)
        self.structure = ImpactMSSFVGLogic()
        self.structure.validate()
        self._minute_history: deque[SignalBar] = deque(maxlen=512)
        self._minute_body_atr: deque[float] = deque(maxlen=512)
        self._minute_swings: deque[MinuteSwing] = deque(maxlen=2048)
        self._minute_last_ts = -1
        self._structural_episode: _StructuralEpisode | None = None
        self._cooldown_until_ns = -1

    @property
    def active_structural_scenario_id(self) -> str | None:
        episode = self._structural_episode
        return episode.scenario_id if episode is not None else None

    def observe_minute(self, bar: SignalBar) -> Observation:
        """Advance an active episode on one completed one-minute bar."""
        if bar.ts_event_ns <= self._minute_last_ts:
            raise ValueError("minute bars must be strictly monotonic")
        self._minute_last_ts = bar.ts_event_ns

        atr = self._minute_atr()
        body_atr = bar.body / atr if atr is not None and atr > 0.0 else float("nan")
        displacement_rank = self._minute_rank(body_atr)
        diagnostics: dict[str, Any] = {
            "clock": "1M",
            "timestamp_ns": bar.ts_event_ns,
            "active_scenario_id": self.active_structural_scenario_id,
            "minute_atr": atr,
            "body_atr": body_atr,
            "displacement_rank": displacement_rank,
            "close_location": bar.close_location,
        }

        plan: TradePlan | None = None
        transitions: list[Transition] = []
        if self._structural_episode is not None:
            plan, transitions = self._advance_structural_episode(
                bar=bar,
                minute_atr=atr,
                body_atr=body_atr,
                displacement_rank=displacement_rank,
            )
            diagnostics["active_scenario_id"] = self.active_structural_scenario_id
            diagnostics["reason"] = (
                transitions[-1].reason_code
                if transitions
                else "STRUCTURAL_EPISODE_ACTIVE"
            )
        else:
            diagnostics["reason"] = "NO_ACTIVE_STRUCTURAL_EPISODE"

        self._minute_history.append(bar)
        if isfinite(body_atr):
            self._minute_body_atr.append(float(body_atr))
        self._confirm_latest_minute_pivot()
        return Observation(plan, tuple(transitions), diagnostics)

    def observe(
        self,
        bar: SignalBar,
        index: int,
        *,
        eligible: bool = True,
    ) -> Observation:
        """Detect only source contacts on the completed five-minute clock."""
        if bar.ts_event_ns <= self._last_ts:
            raise ValueError("signal bars must be strictly monotonic")
        self._last_ts = bar.ts_event_ns

        transitions: list[Transition] = []
        diagnostics: dict[str, Any] = {
            "clock": f"{self.config.signal_minutes}M",
            "index": index,
            "history": len(self._history),
            "eligible": eligible,
            "active_scenario_id": self.active_structural_scenario_id,
        }
        if len(self._history) < self.config.min_history:
            self._history.append(bar)
            diagnostics["reason"] = "WARMUP"
            return Observation(None, tuple(), diagnostics)

        atr = self._atr()
        upper, lower, upper_ts, lower_ts = self._external_levels()
        internal_high, internal_low = self._internal_levels()
        volume_z = self._volume_z(bar.volume)
        efficiency, slope = self._trend_state()
        diagnostics.update(
            {
                "atr": atr,
                "upper_liquidity": upper,
                "lower_liquidity": lower,
                "upper_formed_ns": upper_ts,
                "lower_formed_ns": lower_ts,
                "internal_high": internal_high,
                "internal_low": internal_low,
                "volume_z": volume_z,
                "trend_efficiency": efficiency,
                "trend_slope": slope,
            }
        )

        if not eligible:
            if self._structural_episode is not None:
                transitions.append(
                    self._terminal_transition(
                        self._structural_episode,
                        ScenarioState.INVALIDATED,
                        "ELIGIBILITY_LOST",
                        bar.ts_event_ns,
                        bar.close,
                        {"signal_index": index},
                    )
                )
                self._finish_episode(bar.ts_event_ns)
            self._history.append(bar)
            diagnostics["reason"] = "INELIGIBLE"
            return Observation(None, tuple(transitions), diagnostics)

        if self._structural_episode is not None:
            self._history.append(bar)
            diagnostics["reason"] = "STRUCTURAL_EPISODE_ACTIVE"
            return Observation(None, tuple(), diagnostics)

        if bar.ts_event_ns < self._cooldown_until_ns:
            self._history.append(bar)
            diagnostics["reason"] = "STRUCTURAL_COOLDOWN"
            return Observation(None, tuple(), diagnostics)

        raw_episode, contact = self._detect_contact(
            bar=bar,
            index=index,
            atr=atr,
            volume_z=volume_z,
            efficiency=efficiency,
            slope=slope,
            upper=upper,
            lower=lower,
            internal_high=internal_high,
            internal_low=internal_low,
        )
        self._history.append(bar)
        if raw_episode is None or contact is None:
            diagnostics["reason"] = "NO_QUALIFIED_SOURCE_CONTACT"
            return Observation(None, tuple(), diagnostics)
        if raw_episode.kind is not ScenarioKind.ABSORPTION_RECLAIM:
            transitions.append(contact)
            transitions.append(
                Transition(
                    scenario_id=raw_episode.scenario_id,
                    event_type="STRUCTURAL_TRANSITION",
                    previous_state=ScenarioState.CONTACTED.value,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="GENERIC_BREAKOUT_BRANCH_DISABLED",
                    event_time_ns=bar.ts_event_ns,
                    reference_price=raw_episode.liquidity_level,
                    details={"kind": raw_episode.kind.value},
                )
            )
            diagnostics["reason"] = "GENERIC_BREAKOUT_BRANCH_DISABLED"
            self._set_cooldown(bar.ts_event_ns)
            return Observation(None, tuple(transitions), diagnostics)

        boundary = self._latest_independent_boundary(
            direction=raw_episode.direction,
            source_time_ns=bar.ts_event_ns,
            source_close=bar.close,
        )
        transitions.append(contact)
        if boundary is None:
            transitions.append(
                Transition(
                    scenario_id=raw_episode.scenario_id,
                    event_type="STRUCTURAL_TRANSITION",
                    previous_state=ScenarioState.CONTACTED.value,
                    next_state=ScenarioState.INVALIDATED.value,
                    reason_code="NO_PRE_SWEEP_LOCAL_PROTECTED_SWING",
                    event_time_ns=bar.ts_event_ns,
                    reference_price=raw_episode.liquidity_level,
                    details={
                        "required_side": (
                            "UPPER"
                            if raw_episode.direction is Direction.LONG
                            else "LOWER"
                        ),
                        "structure_context_minutes": (
                            self.structure.displacement_rank_period
                        ),
                    },
                )
            )
            diagnostics["reason"] = "NO_PRE_SWEEP_LOCAL_PROTECTED_SWING"
            self._set_cooldown(bar.ts_event_ns)
            return Observation(None, tuple(transitions), diagnostics)

        self._structural_episode = _StructuralEpisode(
            scenario_id=raw_episode.scenario_id,
            direction=raw_episode.direction,
            source_time_ns=bar.ts_event_ns,
            source_signal_index=index,
            source_level=float(raw_episode.liquidity_level),
            event_extreme=float(raw_episode.extreme),
            event_atr=float(raw_episode.atr),
            opposing_internal=float(raw_episode.opposing_internal),
            opposing_external=float(raw_episode.opposing_external),
            boundary=boundary,
        )
        diagnostics.update(
            {
                "reason": "SOURCE_SWEEP_AWAITS_INDEPENDENT_1M_MSS",
                "scenario_id": raw_episode.scenario_id,
                "direction": raw_episode.direction.value,
                "event_extreme": raw_episode.extreme,
                "boundary_id": boundary.swing_id,
                "boundary_level": boundary.level,
                "boundary_pivot_ns": boundary.pivot_ns,
                "boundary_confirmed_ns": boundary.confirmed_ns,
            }
        )
        return Observation(None, tuple(transitions), diagnostics)

    def _advance_structural_episode(
        self,
        *,
        bar: SignalBar,
        minute_atr: float | None,
        body_atr: float,
        displacement_rank: float,
    ) -> tuple[TradePlan | None, list[Transition]]:
        episode = self._structural_episode
        if episode is None:
            return None, []
        transitions: list[Transition] = []

        if self._source_invalidated(episode, bar):
            transitions.append(
                self._terminal_transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    (
                        "SOURCE_INVALIDATED_BEFORE_MSS"
                        if episode.stage is StructuralStage.AWAIT_MSS
                        else "SOURCE_INVALIDATED_DURING_RETEST"
                    ),
                    bar.ts_event_ns,
                    episode.event_extreme,
                    {"minute_high": bar.high, "minute_low": bar.low},
                )
            )
            self._finish_episode(bar.ts_event_ns)
            return None, transitions

        if episode.stage is StructuralStage.AWAIT_MSS:
            deadline = episode.source_time_ns + (
                self.structure.maximum_mss_minutes * NS_PER_MINUTE
            )
            if bar.ts_event_ns > deadline:
                transitions.append(
                    self._terminal_transition(
                        episode,
                        ScenarioState.INVALIDATED,
                        "INDEPENDENT_1M_MSS_NOT_CONFIRMED_WITHIN_WINDOW",
                        bar.ts_event_ns,
                        episode.boundary.level,
                        {"deadline_ns": deadline},
                    )
                )
                self._finish_episode(bar.ts_event_ns)
                return None, transitions
            if self._mss_confirmed(
                episode,
                bar,
                minute_atr=minute_atr,
                body_atr=body_atr,
                displacement_rank=displacement_rank,
            ):
                episode.stage = StructuralStage.AWAIT_RETEST
                episode.mss_ns = bar.ts_event_ns
                episode.mss_body_atr = body_atr
                episode.mss_rank = displacement_rank
                transitions.append(
                    self._state_transition(
                        episode,
                        ScenarioState.CONFIRMED,
                        "INDEPENDENT_1M_DISPLACEMENT_MSS",
                        bar.ts_event_ns,
                        episode.boundary.level,
                        {
                            "boundary_id": episode.boundary.swing_id,
                            "boundary_level": episode.boundary.level,
                            "body_atr": body_atr,
                            "displacement_rank": displacement_rank,
                            "close_location": bar.close_location,
                        },
                    )
                )
            return None, transitions

        if episode.mss_ns is None:
            raise RuntimeError("retest stage has no MSS timestamp")
        deadline = episode.mss_ns + (
            self.structure.maximum_retest_minutes * NS_PER_MINUTE
        )
        if bar.ts_event_ns > deadline:
            transitions.append(
                self._terminal_transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "SAME_BOUNDARY_1M_RETEST_NOT_CONFIRMED",
                    bar.ts_event_ns,
                    episode.boundary.level,
                    {"deadline_ns": deadline, "mss_ns": episode.mss_ns},
                )
            )
            self._finish_episode(bar.ts_event_ns)
            return None, transitions

        touched, rejected = self._boundary_retest(episode, bar)
        if not touched:
            return None, transitions
        if not rejected:
            transitions.append(
                self._terminal_transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "FIRST_1M_BOUNDARY_RETEST_NOT_DEFENDED",
                    bar.ts_event_ns,
                    episode.boundary.level,
                    {
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "close_location": bar.close_location,
                    },
                )
            )
            self._finish_episode(bar.ts_event_ns)
            return None, transitions

        plan = self._build_structural_plan(episode, bar)
        if plan is None:
            transitions.append(
                self._terminal_transition(
                    episode,
                    ScenarioState.INVALIDATED,
                    "STRUCTURAL_RETEST_GEOMETRY_UNTRADEABLE",
                    bar.ts_event_ns,
                    bar.close,
                    {
                        "entry": bar.close,
                        "event_extreme": episode.event_extreme,
                        "opposing_internal": episode.opposing_internal,
                        "opposing_external": episode.opposing_external,
                    },
                )
            )
            self._finish_episode(bar.ts_event_ns)
            return None, transitions

        transitions.append(
            self._state_transition(
                episode,
                ScenarioState.ENTRY_READY,
                "FIRST_SAME_BOUNDARY_1M_RETEST_REJECTED",
                bar.ts_event_ns,
                plan.entry_reference,
                {
                    "boundary_id": episode.boundary.swing_id,
                    "boundary_level": episode.boundary.level,
                    "mss_ns": episode.mss_ns,
                    "stop": plan.stop_price,
                    "target": plan.target_price,
                    "expected_rr": plan.expected_rr,
                },
            )
        )
        self._finish_episode(bar.ts_event_ns)
        return plan, transitions

    def _latest_independent_boundary(
        self,
        *,
        direction: Direction,
        source_time_ns: int,
        source_close: float,
    ) -> MinuteSwing | None:
        source_bar_start_ns = source_time_ns - (
            (self.config.signal_minutes - 1) * NS_PER_MINUTE
        )
        earliest_ns = source_bar_start_ns - (
            self.structure.displacement_rank_period * NS_PER_MINUTE
        )
        side = "UPPER" if direction is Direction.LONG else "LOWER"
        eligible = [
            swing
            for swing in self._minute_swings
            if swing.side == side
            and earliest_ns <= swing.confirmed_ns < source_bar_start_ns
            and (
                swing.level > source_close
                if direction is Direction.LONG
                else swing.level < source_close
            )
        ]
        if not eligible:
            return None
        return max(
            eligible,
            key=lambda swing: (
                swing.confirmed_ns,
                -abs(swing.level - source_close),
            ),
        )

    def _mss_confirmed(
        self,
        episode: _StructuralEpisode,
        bar: SignalBar,
        *,
        minute_atr: float | None,
        body_atr: float,
        displacement_rank: float,
    ) -> bool:
        if (
            minute_atr is None
            or not isfinite(body_atr)
            or not isfinite(displacement_rank)
        ):
            return False
        body_ok = body_atr >= self.structure.minimum_body_atr
        rank_ok = displacement_rank >= self.structure.minimum_displacement_rank
        if episode.direction is Direction.LONG:
            return (
                bar.close > episode.boundary.level
                and bar.close > bar.open
                and bar.close_location >= self.structure.minimum_close_location
                and body_ok
                and rank_ok
            )
        return (
            bar.close < episode.boundary.level
            and bar.close < bar.open
            and bar.close_location
            <= 1.0 - self.structure.minimum_close_location
            and body_ok
            and rank_ok
        )

    def _boundary_retest(
        self,
        episode: _StructuralEpisode,
        bar: SignalBar,
    ) -> tuple[bool, bool]:
        level = episode.boundary.level
        if episode.direction is Direction.LONG:
            touched = bar.low <= level
            rejected = (
                touched
                and bar.close > level
                and bar.close > bar.open
                and bar.close_location >= self.structure.retest_close_location
            )
        else:
            touched = bar.high >= level
            rejected = (
                touched
                and bar.close < level
                and bar.close < bar.open
                and bar.close_location
                <= 1.0 - self.structure.retest_close_location
            )
        return touched, rejected

    def _source_invalidated(
        self,
        episode: _StructuralEpisode,
        bar: SignalBar,
    ) -> bool:
        buffer = self.structure.stop_buffer_atr * episode.event_atr
        if episode.direction is Direction.LONG:
            return bar.low <= episode.event_extreme - buffer
        return bar.high >= episode.event_extreme + buffer

    def _build_structural_plan(
        self,
        episode: _StructuralEpisode,
        bar: SignalBar,
    ) -> TradePlan | None:
        entry = bar.close
        buffer = self.structure.stop_buffer_atr * episode.event_atr
        if episode.direction is Direction.LONG:
            stop = episode.event_extreme - buffer
            risk = entry - stop
        else:
            stop = episode.event_extreme + buffer
            risk = stop - entry
        if risk <= 0.0:
            return None

        candidates: list[float] = []
        for level in (episode.opposing_internal, episode.opposing_external):
            if episode.direction is Direction.LONG and level > entry:
                candidates.append(level)
            elif episode.direction is Direction.SHORT and level < entry:
                candidates.append(level)
        if not candidates:
            return None
        target_level = (
            min(candidates)
            if episode.direction is Direction.LONG
            else max(candidates)
        )
        structural_rr = abs(target_level - entry) / risk
        if structural_rr < self.config.minimum_rr:
            return None
        expected_rr = min(structural_rr, self.config.maximum_target_rr)
        target = (
            entry + risk * expected_rr
            if episode.direction is Direction.LONG
            else entry - risk * expected_rr
        )
        return TradePlan(
            scenario_id=episode.scenario_id,
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=episode.direction,
            observed_time_ns=bar.ts_event_ns,
            entry_reference=entry,
            stop_price=stop,
            target_price=target,
            liquidity_level=episode.source_level,
            expected_rr=expected_rr,
            details={
                "atr": episode.event_atr,
                "opposing_internal": episode.opposing_internal,
                "opposing_external": episode.opposing_external,
                "structural_route": "5M_SWEEP_1M_MSS_1M_SAME_BOUNDARY_RETEST",
                "source_time_ns": episode.source_time_ns,
                "event_extreme": episode.event_extreme,
                "boundary_id": episode.boundary.swing_id,
                "boundary_level": episode.boundary.level,
                "boundary_pivot_ns": episode.boundary.pivot_ns,
                "boundary_confirmed_ns": episode.boundary.confirmed_ns,
                "mss_ns": episode.mss_ns,
                "mss_body_atr": episode.mss_body_atr,
                "mss_displacement_rank": episode.mss_rank,
                "retest_ns": bar.ts_event_ns,
                "same_boundary_retest": True,
                "source_and_boundary_clocks_distinct": True,
            },
        )

    def _minute_atr(self) -> float | None:
        period = 24
        if len(self._minute_history) < period:
            return None
        bars = list(self._minute_history)[-period:]
        ranges: list[float] = []
        previous_close: float | None = None
        for bar in bars:
            if previous_close is None:
                true_range = bar.range
            else:
                true_range = max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            ranges.append(true_range)
            previous_close = bar.close
        return max(fmean(ranges), bars[-1].close * 1e-6)

    def _minute_rank(self, body_atr: float) -> float:
        period = self.structure.displacement_rank_period
        if not isfinite(body_atr) or len(self._minute_body_atr) < period:
            return float("nan")
        history = list(self._minute_body_atr)[-period:]
        return sum(value <= body_atr for value in history) / len(history)

    def _confirm_latest_minute_pivot(self) -> None:
        radius = self.structure.pivot_radius
        required = radius * 2 + 1
        if len(self._minute_history) < required:
            return
        window = list(self._minute_history)[-required:]
        center = window[radius]
        left = window[:radius]
        right = window[radius + 1 :]
        confirmed_ns = window[-1].ts_event_ns
        if all(center.high > item.high for item in (*left, *right)):
            self._minute_swings.append(
                MinuteSwing(
                    swing_id=f"1MH-{center.ts_event_ns}",
                    side="UPPER",
                    level=center.high,
                    pivot_ns=center.ts_event_ns,
                    confirmed_ns=confirmed_ns,
                )
            )
        if all(center.low < item.low for item in (*left, *right)):
            self._minute_swings.append(
                MinuteSwing(
                    swing_id=f"1ML-{center.ts_event_ns}",
                    side="LOWER",
                    level=center.low,
                    pivot_ns=center.ts_event_ns,
                    confirmed_ns=confirmed_ns,
                )
            )

    def _state_transition(
        self,
        episode: _StructuralEpisode,
        next_state: ScenarioState,
        reason_code: str,
        event_time_ns: int,
        reference_price: float,
        details: dict[str, Any],
    ) -> Transition:
        transition = Transition(
            scenario_id=episode.scenario_id,
            event_type="STRUCTURAL_TRANSITION",
            previous_state=episode.scenario_state.value,
            next_state=next_state.value,
            reason_code=reason_code,
            event_time_ns=event_time_ns,
            reference_price=reference_price,
            details=details,
        )
        episode.scenario_state = next_state
        return transition

    def _terminal_transition(
        self,
        episode: _StructuralEpisode,
        next_state: ScenarioState,
        reason_code: str,
        event_time_ns: int,
        reference_price: float,
        details: dict[str, Any],
    ) -> Transition:
        return self._state_transition(
            episode,
            next_state,
            reason_code,
            event_time_ns,
            reference_price,
            details,
        )

    def _finish_episode(self, event_time_ns: int) -> None:
        self._structural_episode = None
        self._set_cooldown(event_time_ns)

    def _set_cooldown(self, event_time_ns: int) -> None:
        self._cooldown_until_ns = event_time_ns + (
            self.config.episode_cooldown_bars
            * self.config.signal_minutes
            * NS_PER_MINUTE
        )


__all__ = [
    "MinuteSwing",
    "StructuralMSSRouter",
    "StructuralStage",
]
