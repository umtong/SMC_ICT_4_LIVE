"""Causal aggressor-flow entry for the complete EasyChart RE1 system.

Volume is not another global filter. It is an observable auction event which can
replace a missing or delayed visual footprint:

* initiative: above-normal taker imbalance produces above-normal price progress
  in the intended direction;
* absorption: above-normal aggressive flow fails to move price in its own
  direction at a pre-existing decision boundary, and price reclaims or holds;
* repeated absorption: cumulative opposite taker pressure is absorbed over the
  current causal episode while price stops progressing against the trade;
* accepted breaks may enter on the first coherent initiative after the confirmed
  hold, instead of requiring every true breakout to revisit the exact line.

The baseline is the previous sixty completed one-minute bars. The current bar is
never included in its own baseline. Existing OB/FVG and exact-retest paths remain
unchanged and keep priority; flow adds an OR branch rather than another AND gate.
Stops, targets, one-position routing, fees and NAV accounting remain unchanged.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import math
from statistics import median
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioSetup, SetupState, V5TradePlan
from domain import Side
from easychart_re1_complete_policy import LocatedHorizontalFlipEngine
from easychart_re1_human_policy import (
    HumanDecisionAreaEngine,
    HumanHorizontalEngine,
    HumanMajorSwingEngine,
    HumanMicroEngine,
)
from easychart_re1_state import EasyChartRE1StateBundle
from easychart_re1_wedge import TerminalWedgeScenarioEngine


BINANCE_AGGRESSOR_FLOW_RULE = (
    "EXTERNAL_METHOD:"
    "BINANCE_CLOSED_KLINE_QUOTE_VOLUME_TRADE_COUNT_AND_TAKER_BUY_QUOTE_VOLUME_DEFINE_ONE_MINUTE_AGGRESSOR_FLOW"
)
CAUSAL_FLOW_BASELINE_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CURRENT_FLOW_IS_COMPARED_ONLY_WITH_THE_PREVIOUS_SIXTY_COMPLETED_ONE_MINUTE_BARS"
)
FLOW_SUBSTITUTION_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "COHERENT_INITIATIVE_OR_BOUNDARY_ABSORPTION_MAY_REPLACE_A_MISSING_OR_DELAYED_OB_FVG_ENTRY_FOOTPRINT"
)
FLOW_BREAKOUT_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "CONFIRMED_ACCEPTED_BREAK_MAY_ENTER_ON_FIRST_COHERENT_AGGRESSOR_FLOW_WITH_NATURAL_PREENTRY_GEOMETRY"
)
if BINANCE_AGGRESSOR_FLOW_RULE not in _contracts.EXTERNAL_RULES:
    _contracts.EXTERNAL_RULES += (BINANCE_AGGRESSOR_FLOW_RULE,)
for _rule in (CAUSAL_FLOW_BASELINE_RULE, FLOW_SUBSTITUTION_RULE, FLOW_BREAKOUT_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class FlowCandle:
    """OHLCV plus the exact Binance fields needed for aggressor-flow inference."""

    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    quote_volume: float = 0.0
    trade_count: int = 0
    taker_buy_base_volume: float = 0.0
    taker_buy_quote_volume: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.open,
            self.high,
            self.low,
            self.close,
            self.volume,
            self.quote_volume,
            self.taker_buy_base_volume,
            self.taker_buy_quote_volume,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("flow candle values must be finite")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("invalid OHLC geometry")
        if self.trade_count < 0:
            raise ValueError("trade count cannot be negative")


class FlowTriggerKind(str, Enum):
    BUY_INITIATIVE = "FLOW_BUY_INITIATIVE"
    SELL_INITIATIVE = "FLOW_SELL_INITIATIVE"
    SELL_ABSORPTION = "FLOW_SELL_ABSORPTION"
    BUY_ABSORPTION = "FLOW_BUY_ABSORPTION"
    REPEATED_SELL_ABSORPTION = "FLOW_REPEATED_SELL_ABSORPTION"
    REPEATED_BUY_ABSORPTION = "FLOW_REPEATED_BUY_ABSORPTION"


@dataclass(frozen=True, slots=True)
class FlowObservation:
    ts_close_ns: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float
    trade_count: int
    taker_buy_quote_volume: float
    signed_taker_quote: float
    delta_share: float
    body: float
    price_range: float
    close_location: float
    median_quote_volume: float
    median_abs_delta: float
    median_abs_body: float
    median_range: float
    median_trade_size: float
    activity_ratio: float
    delta_ratio: float
    body_ratio: float
    range_ratio: float
    trade_size_ratio: float
    impact_per_activity: float
    active: bool
    directed: bool
    material_progress: bool


@dataclass(frozen=True, slots=True)
class FlowSignal:
    kind: FlowTriggerKind
    mechanism: str
    strength: float
    observation: FlowObservation
    episode_bars: int
    cumulative_signed_taker_quote: float
    net_price_progress: float


class CausalFlowAnalyzer:
    """One-symbol flow state using only completed, prior one-minute bars."""

    BASELINE_BARS = 60
    HISTORY_BARS = 1440

    def __init__(self, tick_size: float) -> None:
        self.tick_size = tick_size
        self.history: deque[FlowObservation] = deque(maxlen=self.HISTORY_BARS)
        self._raw: deque[tuple[float, float, float, float, float]] = deque(
            maxlen=self.HISTORY_BARS,
        )
        self.counts: dict[str, int] = {}
        self.last_observation: FlowObservation | None = None

    def _inc(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    @staticmethod
    def _safe_ratio(value: float, baseline: float, floor: float) -> float:
        return value / max(baseline, floor)

    def observe(self, bar: Any) -> FlowObservation | None:
        quote_volume = float(getattr(bar, "quote_volume", 0.0))
        trade_count = int(getattr(bar, "trade_count", 0))
        taker_buy_quote = float(getattr(bar, "taker_buy_quote_volume", 0.0))
        if (
            quote_volume <= 0.0
            or trade_count <= 0
            or taker_buy_quote < 0.0
            or taker_buy_quote > quote_volume * (1.0 + 1e-9)
        ):
            self._inc("missing_or_invalid_extended_kline")
            self.last_observation = None
            return None

        body = float(bar.close - bar.open)
        price_range = max(float(bar.high - bar.low), self.tick_size)
        signed_delta = 2.0 * taker_buy_quote - quote_volume
        trade_size = quote_volume / trade_count
        close_location = min(1.0, max(0.0, float(bar.close - bar.low) / price_range))

        prior = list(self._raw)[-self.BASELINE_BARS :]
        if len(prior) < self.BASELINE_BARS:
            self._raw.append(
                (quote_volume, abs(signed_delta), abs(body), price_range, trade_size),
            )
            self._inc("baseline_warmup_bar")
            self.last_observation = None
            return None

        median_quote = median(item[0] for item in prior)
        median_abs_delta = median(item[1] for item in prior)
        median_abs_body = median(item[2] for item in prior)
        median_range = median(item[3] for item in prior)
        median_trade_size = median(item[4] for item in prior)
        activity_ratio = self._safe_ratio(quote_volume, median_quote, 1e-12)
        delta_ratio = self._safe_ratio(
            abs(signed_delta),
            median_abs_delta,
            max(quote_volume * 1e-12, 1e-12),
        )
        body_ratio = self._safe_ratio(abs(body), median_abs_body, self.tick_size)
        range_ratio = self._safe_ratio(price_range, median_range, self.tick_size)
        trade_size_ratio = self._safe_ratio(trade_size, median_trade_size, 1e-12)
        impact_per_activity = body_ratio / max(activity_ratio, 1e-12)
        observation = FlowObservation(
            ts_close_ns=int(bar.ts_close_ns),
            open=float(bar.open),
            high=float(bar.high),
            low=float(bar.low),
            close=float(bar.close),
            quote_volume=quote_volume,
            trade_count=trade_count,
            taker_buy_quote_volume=taker_buy_quote,
            signed_taker_quote=signed_delta,
            delta_share=signed_delta / quote_volume,
            body=body,
            price_range=price_range,
            close_location=close_location,
            median_quote_volume=median_quote,
            median_abs_delta=median_abs_delta,
            median_abs_body=median_abs_body,
            median_range=median_range,
            median_trade_size=median_trade_size,
            activity_ratio=activity_ratio,
            delta_ratio=delta_ratio,
            body_ratio=body_ratio,
            range_ratio=range_ratio,
            trade_size_ratio=trade_size_ratio,
            impact_per_activity=impact_per_activity,
            active=quote_volume >= median_quote,
            directed=abs(signed_delta) >= median_abs_delta,
            material_progress=abs(body) >= max(median_abs_body, self.tick_size),
        )
        self._raw.append(
            (quote_volume, abs(signed_delta), abs(body), price_range, trade_size),
        )
        self.history.append(observation)
        self.last_observation = observation
        self._inc("baseline_ready_bar")
        return observation

    def since(self, time_ns: int) -> list[FlowObservation]:
        return [item for item in self.history if item.ts_close_ns > time_ns]

    @property
    def diagnostics(self) -> dict[str, Any]:
        last = self.last_observation
        return {
            "counts": dict(sorted(self.counts.items())),
            "baseline_bars": self.BASELINE_BARS,
            "history_bars": len(self.history),
            "last": None
            if last is None
            else {
                "ts_close_ns": last.ts_close_ns,
                "activity_ratio": last.activity_ratio,
                "delta_ratio": last.delta_ratio,
                "delta_share": last.delta_share,
                "body_ratio": last.body_ratio,
                "trade_size_ratio": last.trade_size_ratio,
                "impact_per_activity": last.impact_per_activity,
            },
            "rules": (
                BINANCE_AGGRESSOR_FLOW_RULE,
                CAUSAL_FLOW_BASELINE_RULE,
            ),
        }


class FlowEntryMixin:
    """Add a flow OR-branch while preserving all existing visual entry paths."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.flow_analyzer = CausalFlowAnalyzer(self.tick_size)
        self._flow_current: FlowObservation | None = None
        self._flow_plans: list[V5TradePlan] = []
        self._flow_counts: dict[str, int] = {}

    def _finc(self, key: str) -> None:
        self._flow_counts[key] = self._flow_counts.get(key, 0) + 1

    @staticmethod
    def _intended_progress(side: Side, start: float, end: float) -> float:
        return end - start if side is Side.LONG else start - end

    @staticmethod
    def _opposite_delta(side: Side, value: float) -> bool:
        return value < 0.0 if side is Side.LONG else value > 0.0

    @staticmethod
    def _aligned_delta(side: Side, value: float) -> bool:
        return value > 0.0 if side is Side.LONG else value < 0.0

    def _flow_signal(
        self,
        setup: ScenarioSetup,
        bar: Any,
        observation: FlowObservation | None,
    ) -> FlowSignal | None:
        if observation is None or not observation.active or not observation.directed:
            return None
        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        if setup.side is Side.LONG:
            outside = bar.close > upper
            aligned_body = bar.close > bar.open
            intended_half = observation.close_location >= 0.5
            touches = bar.low <= upper
        else:
            outside = bar.close < lower
            aligned_body = bar.close < bar.open
            intended_half = observation.close_location <= 0.5
            touches = bar.high >= lower
        if not outside or not aligned_body:
            return None

        event_start = setup.confirmation_time_ns or setup.interaction_time_ns
        episode = self.flow_analyzer.since(event_start)
        cumulative_delta = sum(item.signed_taker_quote for item in episode)
        net_progress = 0.0
        if episode:
            net_progress = self._intended_progress(
                setup.side,
                episode[0].open,
                episode[-1].close,
            )

        current_absorption = (
            touches
            and self._opposite_delta(setup.side, observation.signed_taker_quote)
        )
        if current_absorption:
            kind = (
                FlowTriggerKind.SELL_ABSORPTION
                if setup.side is Side.LONG
                else FlowTriggerKind.BUY_ABSORPTION
            )
            return FlowSignal(
                kind=kind,
                mechanism="CURRENT_BOUNDARY_ABSORPTION",
                strength=observation.activity_ratio * observation.delta_ratio,
                observation=observation,
                episode_bars=len(episode),
                cumulative_signed_taker_quote=cumulative_delta,
                net_price_progress=net_progress,
            )

        repeated = False
        if len(episode) >= 2:
            opposite_dominant = any(
                item.active
                and item.directed
                and self._opposite_delta(setup.side, item.signed_taker_quote)
                for item in episode
            )
            touch_seen = any(
                item.low <= upper if setup.side is Side.LONG else item.high >= lower
                for item in episode
            )
            cumulative_opposite = self._opposite_delta(setup.side, cumulative_delta)
            repeated = (
                opposite_dominant
                and touch_seen
                and cumulative_opposite
                and net_progress >= 0.0
            )
        if repeated:
            kind = (
                FlowTriggerKind.REPEATED_SELL_ABSORPTION
                if setup.side is Side.LONG
                else FlowTriggerKind.REPEATED_BUY_ABSORPTION
            )
            return FlowSignal(
                kind=kind,
                mechanism="REPEATED_BOUNDARY_ABSORPTION",
                strength=observation.activity_ratio * observation.delta_ratio,
                observation=observation,
                episode_bars=len(episode),
                cumulative_signed_taker_quote=cumulative_delta,
                net_price_progress=net_progress,
            )

        initiative = (
            self._aligned_delta(setup.side, observation.signed_taker_quote)
            and observation.material_progress
            and intended_half
        )
        if not initiative:
            return None
        kind = (
            FlowTriggerKind.BUY_INITIATIVE
            if setup.side is Side.LONG
            else FlowTriggerKind.SELL_INITIATIVE
        )
        return FlowSignal(
            kind=kind,
            mechanism="COHERENT_INITIATIVE",
            strength=(
                observation.activity_ratio
                * observation.delta_ratio
                * observation.body_ratio
            ),
            observation=observation,
            episode_bars=len(episode),
            cumulative_signed_taker_quote=cumulative_delta,
            net_price_progress=net_progress,
        )

    def _flow_proxy(self, setup: ScenarioSetup, time_ns: int) -> Any:
        try:
            return self.structure.snapshot_for(setup.context, time_ns)
        except (AttributeError, KeyError, LookupError, RuntimeError, StopIteration):
            return setup.context

    @staticmethod
    def _signal_trace(signal: FlowSignal) -> dict[str, Any]:
        item = signal.observation
        return {
            "flow_kind": signal.kind.value,
            "flow_mechanism": signal.mechanism,
            "flow_strength": signal.strength,
            "flow_quote_volume": item.quote_volume,
            "flow_median_quote_volume": item.median_quote_volume,
            "flow_activity_ratio": item.activity_ratio,
            "flow_trade_count": item.trade_count,
            "flow_trade_size_ratio": item.trade_size_ratio,
            "flow_taker_buy_quote_volume": item.taker_buy_quote_volume,
            "flow_signed_taker_quote": item.signed_taker_quote,
            "flow_delta_share": item.delta_share,
            "flow_delta_ratio": item.delta_ratio,
            "flow_body_ratio": item.body_ratio,
            "flow_range_ratio": item.range_ratio,
            "flow_close_location": item.close_location,
            "flow_impact_per_activity": item.impact_per_activity,
            "flow_episode_bars": signal.episode_bars,
            "flow_episode_cumulative_delta": signal.cumulative_signed_taker_quote,
            "flow_episode_net_price_progress": signal.net_price_progress,
        }

    def _create_flow_plan(
        self,
        setup: ScenarioSetup,
        bar: Any,
        signal: FlowSignal,
        *,
        acceptance: bool,
    ) -> V5TradePlan | None:
        if self._target_is_spent(setup, bar):
            self._finish(
                setup,
                SetupState.TARGET_SPENT,
                bar.ts_close_ns,
                "target_spent_before_flow_entry",
            )
            return None
        if not acceptance and self._extreme_breached(setup, bar):
            self._finish(
                setup,
                SetupState.INVALIDATED,
                bar.ts_close_ns,
                "interaction_extreme_breached_before_flow_entry",
            )
            return None

        if acceptance:
            stop = self._acceptance_stop(setup, bar.ts_close_ns)
            if stop is None:
                self._finish(
                    setup,
                    SetupState.NO_TRADE_GEOMETRY,
                    bar.ts_close_ns,
                    "flow_acceptance_missing_stop",
                )
                return None
        else:
            stop = (
                setup.interaction_extreme - self.tick_size
                if setup.side is Side.LONG
                else setup.interaction_extreme + self.tick_size
            )

        state_before = setup.state
        proxy = self._flow_proxy(setup, bar.ts_close_ns)
        self._audit(proxy)
        plan = self._make_plan(
            setup,
            bar,
            entry=bar.close,
            stop=stop,
            trigger_zone=proxy,
            trigger_kind=signal.kind,
            trigger_strength=signal.strength,
        )
        if plan is None:
            self._finc("flow_geometry_rejected")
            return None
        self._flow_plans.append(plan)
        if acceptance:
            self._finc("flow_acceptance_plan_created")
        elif state_before is SetupState.WAITING_FOOTPRINT_RETEST:
            self._finc("flow_accelerated_delayed_footprint")
        else:
            self._finc("flow_substituted_missing_footprint")
        self._finc(f"signal_{signal.kind.value.lower()}")
        self._trace(
            "flow_plan_created",
            bar.ts_close_ns,
            setup,
            plan_id=plan.plan_id,
            state_before_flow=state_before.value,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            gross_rr=plan.gross_rr,
            acceptance=acceptance,
            rule_provenance=(FLOW_SUBSTITUTION_RULE, FLOW_BREAKOUT_RULE),
            **self._signal_trace(signal),
        )
        return plan

    def _arm_displacements(
        self,
        bar: Any,
        index: int,
        created: list[Any],
    ) -> None:
        self._flow_current = self.flow_analyzer.observe(bar)
        candidates = [
            setup
            for setup in list(self._active.values())
            if setup.state is SetupState.WAITING_DISPLACEMENT
            and setup.confirmation_time_ns is not None
            and bar.ts_close_ns > setup.confirmation_time_ns
        ]
        signals = {
            setup.setup_id: self._flow_signal(setup, bar, self._flow_current)
            for setup in candidates
        }

        # Existing strong-OB and normal OB/FVG paths keep priority.
        super()._arm_displacements(bar, index, created)

        for original in candidates:
            signal = signals.get(original.setup_id)
            if signal is None:
                continue
            setup = self._active.get(original.setup_id)
            if setup is None or setup.state not in {
                SetupState.WAITING_DISPLACEMENT,
                SetupState.WAITING_FOOTPRINT_RETEST,
            }:
                continue
            self._create_flow_plan(setup, bar, signal, acceptance=False)

    def _advance_acceptance_retests(self, bar: Any, index: int) -> list[V5TradePlan]:
        output = super()._advance_acceptance_retests(bar, index)
        observation = self._flow_current
        if observation is None:
            return output
        candidates = [
            setup
            for setup in list(self._active.values())
            if setup.state is SetupState.WAITING_ACCEPTANCE_RETEST
            and setup.confirmation_time_ns is not None
            and bar.ts_close_ns > setup.confirmation_time_ns
        ]
        for setup in candidates:
            signal = self._flow_signal(setup, bar, observation)
            if signal is None:
                continue
            self._create_flow_plan(setup, bar, signal, acceptance=True)
        return output

    def on_bar(self, timeframe_minutes: int, bar: Any) -> list[V5TradePlan]:
        if timeframe_minutes != self.trigger_minutes:
            return super().on_bar(timeframe_minutes, bar)
        self._flow_plans = []
        existing = super().on_bar(timeframe_minutes, bar)
        unique = {plan.plan_id: plan for plan in existing + self._flow_plans}
        return sorted(
            unique.values(),
            key=lambda plan: (
                plan.interaction_time_ns,
                plan.observed_time_ns,
                plan.plan_id,
            ),
        )

    @property
    def flow_entry_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._flow_counts.items())),
            "analyzer": self.flow_analyzer.diagnostics,
            "rules": (
                BINANCE_AGGRESSOR_FLOW_RULE,
                CAUSAL_FLOW_BASELINE_RULE,
                FLOW_SUBSTITUTION_RULE,
                FLOW_BREAKOUT_RULE,
            ),
        }


class FlowHumanMicroEngine(FlowEntryMixin, HumanMicroEngine):
    pass


class FlowHumanHorizontalEngine(FlowEntryMixin, HumanHorizontalEngine):
    pass


class FlowHumanMajorSwingEngine(FlowEntryMixin, HumanMajorSwingEngine):
    pass


class FlowHumanDecisionAreaEngine(FlowEntryMixin, HumanDecisionAreaEngine):
    pass


class FlowHorizontalFlipEngine(FlowEntryMixin, LocatedHorizontalFlipEngine):
    pass


class FlowTerminalWedgeScenarioEngine(FlowEntryMixin, TerminalWedgeScenarioEngine):
    pass


class EasyChartRE1FlowBundle(EasyChartRE1StateBundle):
    """Causal state system with flow as an independent entry mechanism."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = FlowHumanMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = FlowHumanHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = FlowHumanMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.decision_area = FlowHumanDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal_flip = FlowHorizontalFlipEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.wedge = FlowTerminalWedgeScenarioEngine(
            symbol,
            tick_size,
            scale_name="TERMINAL_WEDGE",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        for key in (
            "micro",
            "horizontal",
            "major_swing",
            "decision_area",
            "horizontal_flip",
            "wedge",
        ):
            self._audit_offsets[key] = 0

    @property
    def diagnostics(self) -> dict[str, Any]:
        output = dict(super().diagnostics)
        output["causal_aggressor_flow_policy"] = {
            "micro": self.micro.flow_entry_diagnostics,
            "horizontal": self.horizontal.flow_entry_diagnostics,
            "major_swing": self.major_swing.flow_entry_diagnostics,
            "decision_area": self.decision_area.flow_entry_diagnostics,
            "horizontal_flip": self.horizontal_flip.flow_entry_diagnostics,
            "terminal_wedge": self.wedge.flow_entry_diagnostics,
            "rules": (
                BINANCE_AGGRESSOR_FLOW_RULE,
                CAUSAL_FLOW_BASELINE_RULE,
                FLOW_SUBSTITUTION_RULE,
                FLOW_BREAKOUT_RULE,
            ),
        }
        return output


MultiScaleScenarioBundle = EasyChartRE1FlowBundle
