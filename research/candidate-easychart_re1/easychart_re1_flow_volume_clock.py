"""Volume-clock auction episodes for causal EasyChart RE1 entry timing.

A large execution program is not obliged to finish inside one wall-clock minute.
The first flow candidate therefore over-traded ordinary one-bar initiative. This
module synchronizes the event to traded quote volume instead:

* for each causal setup, accumulate completed one-minute flow until the episode
  contains one typical prior-minute quote volume;
* evaluate that first completed volume bucket exactly once;
* accepted breaks require cumulative aligned taker flow and material price
  progress beyond the boundary;
* rejection, bounce and rotation require cumulative aggression against the
  trade which fails to create adverse price progress and finishes reclaimed;
* if the first volume bucket does not tell a coherent auction story, no later
  outcome is searched for--the inherited visual OB/FVG/retest path remains.

This reuses volume-synchronized sampling from market-microstructure research as
an event clock, not as a fitted threshold or global trade filter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import contracts_v5 as _contracts
from contracts_v5 import ScenarioPath, ScenarioSetup
from domain import Side
from easychart_re1_complete_policy import LocatedHorizontalFlipEngine
from easychart_re1_flow import (
    FlowEntryMixin,
    FlowObservation,
    FlowSignal,
    FlowTriggerKind,
)
from easychart_re1_flow_routed import EasyChartRE1FlowRoutedBundle
from easychart_re1_human_policy import (
    HumanDecisionAreaEngine,
    HumanHorizontalEngine,
    HumanMajorSwingEngine,
    HumanMicroEngine,
)
from easychart_re1_wedge import TerminalWedgeScenarioEngine


VOLUME_CLOCK_FLOW_RULE = (
    "EXTERNAL_METHOD:"
    "FIRST_TYPICAL_PRIOR_MINUTE_OF_QUOTE_VOLUME_DEFINES_THE_SETUP_FLOW_BUCKET"
)
FIRST_BUCKET_ONLY_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "EACH_CAUSAL_SETUP_EVALUATES_ONLY_ITS_FIRST_COMPLETED_VOLUME_BUCKET_AND_DOES_NOT_SEARCH_LATER_OUTCOMES"
)
VOLUME_CLOCK_MECHANISM_RULE = (
    "RESEARCH_HYPOTHESIS:"
    "ACCEPTANCE_REQUIRES_CUMULATIVE_INITIATIVE_WHILE_REVERSAL_REQUIRES_CUMULATIVE_OPPOSING_AGGRESSION_WITH_NONADVERSE_PRICE_PROGRESS"
)
if VOLUME_CLOCK_FLOW_RULE not in _contracts.EXTERNAL_RULES:
    _contracts.EXTERNAL_RULES += (VOLUME_CLOCK_FLOW_RULE,)
for _rule in (FIRST_BUCKET_ONLY_RULE, VOLUME_CLOCK_MECHANISM_RULE):
    if _rule not in _contracts.RESEARCH_RULES:
        _contracts.RESEARCH_RULES += (_rule,)


@dataclass(frozen=True, slots=True)
class VolumeClockFlowSignal(FlowSignal):
    cumulative_quote_volume: float
    volume_clock_ratio: float
    cumulative_delta_ratio: float


class VolumeClockFlowEntryMixin(FlowEntryMixin):
    """Classify the first typical-volume auction bucket for each setup."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._volume_bucket_evaluated: set[str] = set()
        self._volume_clock_counts: dict[str, int] = {}

    def _vcinc(self, key: str) -> None:
        self._volume_clock_counts[key] = self._volume_clock_counts.get(key, 0) + 1

    def _flow_signal(
        self,
        setup: ScenarioSetup,
        bar: Any,
        observation: FlowObservation | None,
    ) -> VolumeClockFlowSignal | None:
        if observation is None or setup.setup_id in self._volume_bucket_evaluated:
            return None
        event_start = setup.confirmation_time_ns or setup.interaction_time_ns
        episode = self.flow_analyzer.since(event_start)
        if not episode:
            return None

        cumulative_quote = sum(item.quote_volume for item in episode)
        typical_quote = max(observation.median_quote_volume, 1e-12)
        if cumulative_quote < typical_quote:
            self._vcinc("volume_bucket_accumulating")
            return None

        # The first completed volume bucket is evaluated once, whether or not it
        # produces a signal. This prevents waiting for a favorable later outcome.
        self._volume_bucket_evaluated.add(setup.setup_id)
        self._vcinc("first_volume_bucket_evaluated")

        cumulative_delta = sum(item.signed_taker_quote for item in episode)
        delta_floor = max(observation.median_abs_delta, typical_quote * 1e-12, 1e-12)
        delta_ratio = abs(cumulative_delta) / delta_floor
        if abs(cumulative_delta) < observation.median_abs_delta:
            self._vcinc("first_volume_bucket_without_material_delta")
            return None

        _, lower, upper = self._projected_bounds(setup, bar.ts_close_ns)
        if setup.side is Side.LONG:
            outside = bar.close > upper
            intended_half = observation.close_location >= 0.5
            touch_seen = any(item.low <= upper for item in episode)
        else:
            outside = bar.close < lower
            intended_half = observation.close_location <= 0.5
            touch_seen = any(item.high >= lower for item in episode)
        if not outside:
            self._vcinc("first_volume_bucket_not_reclaimed_or_accepted")
            return None

        net_progress = self._intended_progress(
            setup.side,
            episode[0].open,
            episode[-1].close,
        )
        volume_ratio = cumulative_quote / typical_quote
        if setup.path is ScenarioPath.ACCEPTANCE:
            coherent = (
                self._aligned_delta(setup.side, cumulative_delta)
                and net_progress >= max(observation.median_abs_body, self.tick_size)
                and intended_half
            )
            if not coherent:
                self._vcinc("acceptance_first_bucket_without_initiative")
                return None
            kind = (
                FlowTriggerKind.BUY_INITIATIVE
                if setup.side is Side.LONG
                else FlowTriggerKind.SELL_INITIATIVE
            )
            self._vcinc("volume_clock_initiative_signal")
            return VolumeClockFlowSignal(
                kind=kind,
                mechanism="VOLUME_CLOCK_INITIATIVE",
                strength=volume_ratio * delta_ratio * (
                    net_progress / max(observation.median_abs_body, self.tick_size)
                ),
                observation=observation,
                episode_bars=len(episode),
                cumulative_signed_taker_quote=cumulative_delta,
                net_price_progress=net_progress,
                cumulative_quote_volume=cumulative_quote,
                volume_clock_ratio=volume_ratio,
                cumulative_delta_ratio=delta_ratio,
            )

        if setup.path not in {
            ScenarioPath.REJECTION,
            ScenarioPath.BOUNCE,
            ScenarioPath.ROTATION,
        }:
            self._vcinc("unknown_path_first_bucket_rejected")
            return None
        coherent = (
            self._opposite_delta(setup.side, cumulative_delta)
            and touch_seen
            and net_progress >= 0.0
        )
        if not coherent:
            self._vcinc("reversal_first_bucket_without_absorption")
            return None
        kind = (
            FlowTriggerKind.REPEATED_SELL_ABSORPTION
            if setup.side is Side.LONG
            else FlowTriggerKind.REPEATED_BUY_ABSORPTION
        )
        self._vcinc("volume_clock_absorption_signal")
        return VolumeClockFlowSignal(
            kind=kind,
            mechanism="VOLUME_CLOCK_ABSORPTION",
            strength=volume_ratio * delta_ratio,
            observation=observation,
            episode_bars=len(episode),
            cumulative_signed_taker_quote=cumulative_delta,
            net_price_progress=net_progress,
            cumulative_quote_volume=cumulative_quote,
            volume_clock_ratio=volume_ratio,
            cumulative_delta_ratio=delta_ratio,
        )

    @staticmethod
    def _signal_trace(signal: FlowSignal) -> dict[str, Any]:
        output = FlowEntryMixin._signal_trace(signal)
        if isinstance(signal, VolumeClockFlowSignal):
            output.update(
                {
                    "flow_cumulative_quote_volume": signal.cumulative_quote_volume,
                    "flow_volume_clock_ratio": signal.volume_clock_ratio,
                    "flow_cumulative_delta_ratio": signal.cumulative_delta_ratio,
                },
            )
        return output

    @property
    def volume_clock_diagnostics(self) -> dict[str, Any]:
        return {
            "counts": dict(sorted(self._volume_clock_counts.items())),
            "evaluated_setups": len(self._volume_bucket_evaluated),
            "rules": (
                VOLUME_CLOCK_FLOW_RULE,
                FIRST_BUCKET_ONLY_RULE,
                VOLUME_CLOCK_MECHANISM_RULE,
            ),
        }


class VolumeClockMicroEngine(VolumeClockFlowEntryMixin, HumanMicroEngine):
    pass


class VolumeClockHorizontalEngine(VolumeClockFlowEntryMixin, HumanHorizontalEngine):
    pass


class VolumeClockMajorSwingEngine(VolumeClockFlowEntryMixin, HumanMajorSwingEngine):
    pass


class VolumeClockDecisionAreaEngine(
    VolumeClockFlowEntryMixin,
    HumanDecisionAreaEngine,
):
    pass


class VolumeClockHorizontalFlipEngine(
    VolumeClockFlowEntryMixin,
    LocatedHorizontalFlipEngine,
):
    pass


class VolumeClockTerminalWedgeEngine(
    VolumeClockFlowEntryMixin,
    TerminalWedgeScenarioEngine,
):
    pass


class EasyChartRE1VolumeClockFlowBundle(EasyChartRE1FlowRoutedBundle):
    """Routed flow system using one first typical-volume bucket per setup."""

    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        kwargs = {"minimum_gross_rr": minimum_gross_rr}
        self.micro = VolumeClockMicroEngine(
            symbol,
            tick_size,
            scale_name="MICRO",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal = VolumeClockHorizontalEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.major_swing = VolumeClockMajorSwingEngine(
            symbol,
            tick_size,
            scale_name="LIQUIDITY",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.decision_area = VolumeClockDecisionAreaEngine(
            symbol,
            tick_size,
            scale_name="DECISION_AREA_OB",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.horizontal_flip = VolumeClockHorizontalFlipEngine(
            symbol,
            tick_size,
            scale_name="HORIZONTAL_SR_FLIP",
            higher_minutes=15,
            decision_minutes=5,
            trigger_minutes=1,
            **kwargs,
        )
        self.wedge = VolumeClockTerminalWedgeEngine(
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
        engines = {
            "micro": self.micro,
            "horizontal": self.horizontal,
            "major_swing": self.major_swing,
            "decision_area": self.decision_area,
            "horizontal_flip": self.horizontal_flip,
            "terminal_wedge": self.wedge,
        }
        output["volume_clock_flow_policy"] = {
            name: engine.volume_clock_diagnostics
            for name, engine in engines.items()
        }
        output["volume_clock_flow_policy"]["rules"] = (
            VOLUME_CLOCK_FLOW_RULE,
            FIRST_BUCKET_ONLY_RULE,
            VOLUME_CLOCK_MECHANISM_RULE,
        )
        return output


MultiScaleScenarioBundle = EasyChartRE1VolumeClockFlowBundle
