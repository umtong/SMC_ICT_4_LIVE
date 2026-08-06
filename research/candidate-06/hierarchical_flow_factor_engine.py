"""Stage-factorized order-flow evidence for the confirmed swing-pool relay."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from typing import Any, Iterator, Mapping

from hierarchical_pool_engine import HierarchicalConfirmedPoolContinuationEngine
from hierarchical_sweep_engine import _AuctionBar, _Bias, _SweepEpisode
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition


class HierarchicalFlowFactorizedEngine(HierarchicalConfirmedPoolContinuationEngine):
    """Factor signed-flow evidence by causal stage instead of all-or-nothing.

    The parent hierarchy has three conceptually distinct uses of taker flow:

    1. HTF acceptance: did aggressive participation support the completed BOS?
    2. LTF sweep: was the liquidity take driven by counter-bias aggression?
    3. LTF response: did aggression realign with the accepted HTF direction?

    A single global switch confounds those roles. This subclass preserves every
    price, timing, pool, state, target and execution rule while allowing each
    stage to be independently enabled. The temporary legacy flag is scoped to
    one synchronous state-machine call and restored immediately.
    """

    def _stage_flag(self, key: str) -> bool:
        return bool(self.params.get(key, self.params.get("hsc_use_flow_proxy", True)))

    @contextmanager
    def _legacy_flow_scope(self, enabled: bool) -> Iterator[None]:
        key = "hsc_use_flow_proxy"
        present = key in self.params
        previous = self.params.get(key)
        self.params[key] = bool(enabled)
        try:
            yield
        finally:
            if present:
                self.params[key] = previous
            else:
                self.params.pop(key, None)

    def _evaluate_completed_bias(
        self,
        bar: _AuctionBar,
        snapshot: PrimitiveSnapshot,
    ) -> tuple[ScenarioTransition, ...]:
        with self._legacy_flow_scope(self._stage_flag("hff_use_bias_flow")):
            return super()._evaluate_completed_bias(bar, snapshot)

    def _maybe_start_sweep(self, snapshot: PrimitiveSnapshot) -> ScenarioTransition | None:
        with self._legacy_flow_scope(self._stage_flag("hff_use_sweep_flow")):
            return super()._maybe_start_sweep(snapshot)

    def _advance_sweep(self, snapshot: PrimitiveSnapshot, *, allow_new: bool) -> ScenarioStep:
        with self._legacy_flow_scope(self._stage_flag("hff_use_response_flow")):
            return super()._advance_sweep(snapshot, allow_new=allow_new)

    def _emit(self, snapshot: PrimitiveSnapshot, bias: _Bias, sweep: _SweepEpisode) -> ScenarioStep:
        step = super()._emit(snapshot, bias, sweep)
        if step.signal is None:
            return step
        details = {
            **dict(step.signal.details),
            "flow_stage_contract": {
                "bias": self._stage_flag("hff_use_bias_flow"),
                "sweep": self._stage_flag("hff_use_sweep_flow"),
                "response": self._stage_flag("hff_use_response_flow"),
            },
        }
        signal: ScenarioSignal = replace(step.signal, family="HFF", details=details)
        return ScenarioStep(transitions=step.transitions, signal=signal)
