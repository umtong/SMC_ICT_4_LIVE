"""Crowding inventory-response bifurcation for candidate-06.

The initiating event remains the OIDB causal primitive: an extreme completed
five-minute open-interest contraction accompanied by aligned price and taker
flow.  This engine adds one independent cause classification using the completed
all-account long/short ratio at the same metrics timestamp:

* DISCHARGE: account composition moves with the price shock, consistent with
  positions on the displaced side being closed.  The inherited later response
  may either exhaust/reclaim or continue while OI keeps contracting.
* COUNTER_INVENTORY: account composition moves against the price shock.  A
  reversal is forbidden.  Continuation is allowed only after a later completed
  metrics observation shows OI rebuilding while the counter-positioning remains
  and price extends in the original shock direction.

The account-composition sign is used as a causal branch, not as a fitted score.
Robust prior-only z-scores are recorded for diagnosis but never gate a trade.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from math import log
from statistics import median
from typing import Any, Mapping

from futures_metrics_data import FuturesMetric
from lrb_types import PrimitiveSnapshot, ScenarioSignal, ScenarioStep, ScenarioTransition
from open_interest_deleveraging_engine import (
    OpenInterestDeleveragingBifurcationEngine,
    _Wave,
)


@dataclass(frozen=True, slots=True)
class _CrowdingContext:
    branch: str
    shock_sign: float
    event_ts_ns: int
    prior_open_interest: float
    event_open_interest: float
    prior_all_account_ratio: float
    event_all_account_ratio: float
    all_account_delta_log: float
    all_account_level_z_prior_only: float | None
    all_account_delta_z_prior_only: float | None
    top_account_delta_log: float
    top_account_delta_z_prior_only: float | None
    top_position_delta_log: float
    top_position_delta_z_prior_only: float | None


class CrowdingInventoryResponseBifurcationEngine(
    OpenInterestDeleveragingBifurcationEngine,
):
    """Trade OI shocks only after classifying inventory transfer causally."""

    def __init__(
        self,
        params: Mapping[str, Any],
        *,
        metrics: Mapping[int, FuturesMetric],
    ) -> None:
        super().__init__(params, metrics=metrics)
        history = max(48, int(self.params.get("cirb_crowding_history_points", 288)))
        self._all_account_levels: deque[float] = deque(maxlen=history)
        self._all_account_changes: deque[float] = deque(maxlen=history)
        self._top_account_changes: deque[float] = deque(maxlen=history)
        self._top_position_changes: deque[float] = deque(maxlen=history)
        self._crowding_context: dict[str, _CrowdingContext] = {}

    @staticmethod
    def _safe_log_ratio(value: float) -> float:
        if value <= 0.0:
            raise ValueError(f"long/short ratio must be positive, got {value}")
        return log(value)

    @staticmethod
    def _robust_z(value: float, history: deque[float]) -> float | None:
        minimum = 36
        if len(history) < minimum:
            return None
        values = list(history)
        centre = median(values)
        mad = median(abs(item - centre) for item in values)
        if mad <= 1e-12:
            return 0.0
        return (value - centre) / (1.4826 * mad)

    @staticmethod
    def _shock_sign(wave: _Wave) -> float:
        return 1.0 if wave.side == "BUY" else -1.0

    def _context_details(self, wave: _Wave) -> dict[str, Any]:
        context = self._crowding_context.get(wave.scenario_id)
        if context is None:
            return {"crowding_branch": "MISSING"}
        return {
            "crowding_branch": context.branch,
            "shock_sign": context.shock_sign,
            "prior_open_interest": context.prior_open_interest,
            "event_open_interest": context.event_open_interest,
            "prior_all_account_ratio": context.prior_all_account_ratio,
            "event_all_account_ratio": context.event_all_account_ratio,
            "all_account_delta_log": context.all_account_delta_log,
            "all_account_level_z_prior_only": context.all_account_level_z_prior_only,
            "all_account_delta_z_prior_only": context.all_account_delta_z_prior_only,
            "top_account_delta_log": context.top_account_delta_log,
            "top_account_delta_z_prior_only": context.top_account_delta_z_prior_only,
            "top_position_delta_log": context.top_position_delta_log,
            "top_position_delta_z_prior_only": context.top_position_delta_z_prior_only,
        }

    def _maybe_start_metric_wave(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric,
    ) -> ScenarioTransition | None:
        prior = self._last_metric
        transition = super()._maybe_start_metric_wave(snapshot, metric)
        wave = self._wave
        if transition is None or wave is None or prior is None:
            return transition

        old_id = wave.scenario_id
        wave.scenario_id = old_id.replace("OIDB-", "CIRB-", 1)
        shock_sign = self._shock_sign(wave)
        event_all = self._safe_log_ratio(metric.all_account_long_short)
        prior_all = self._safe_log_ratio(prior.all_account_long_short)
        all_delta = event_all - prior_all
        top_account_delta = (
            self._safe_log_ratio(metric.top_account_long_short)
            - self._safe_log_ratio(prior.top_account_long_short)
        )
        top_position_delta = (
            self._safe_log_ratio(metric.top_position_long_short)
            - self._safe_log_ratio(prior.top_position_long_short)
        )

        if not bool(self.params.get("cirb_use_all_account_composition", True)):
            branch = "LEGACY"
        else:
            aligned = shock_sign * all_delta
            if aligned > 0.0:
                branch = "DISCHARGE"
            elif aligned < 0.0:
                branch = "COUNTER_INVENTORY"
            else:
                branch = "AMBIGUOUS"

        context = _CrowdingContext(
            branch=branch,
            shock_sign=shock_sign,
            event_ts_ns=snapshot.observation.ts_ns,
            prior_open_interest=prior.open_interest,
            event_open_interest=metric.open_interest,
            prior_all_account_ratio=prior.all_account_long_short,
            event_all_account_ratio=metric.all_account_long_short,
            all_account_delta_log=all_delta,
            all_account_level_z_prior_only=self._robust_z(prior_all, self._all_account_levels),
            all_account_delta_z_prior_only=self._robust_z(all_delta, self._all_account_changes),
            top_account_delta_log=top_account_delta,
            top_account_delta_z_prior_only=self._robust_z(
                top_account_delta,
                self._top_account_changes,
            ),
            top_position_delta_log=top_position_delta,
            top_position_delta_z_prior_only=self._robust_z(
                top_position_delta,
                self._top_position_changes,
            ),
        )
        self._crowding_context[wave.scenario_id] = context
        details = {**dict(transition.details), **self._context_details(wave)}
        reason = {
            "DISCHARGE": "EXTREME_OI_CONTRACTION_WITH_ALIGNED_FLOW_AND_CROWD_DISCHARGE",
            "COUNTER_INVENTORY": "EXTREME_OI_CONTRACTION_WITH_ALIGNED_FLOW_AND_COUNTER_INVENTORY",
            "AMBIGUOUS": "EXTREME_OI_CONTRACTION_WITH_AMBIGUOUS_ACCOUNT_COMPOSITION",
            "LEGACY": transition.reason_code,
        }[branch]
        return replace(
            transition,
            scenario_id=wave.scenario_id,
            event_type="CROWDING_INVENTORY_RESPONSE_TRANSITION",
            reason_code=reason,
            details=details,
        )

    def _ingest_metric_history(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric | None,
    ) -> None:
        prior = self._last_metric
        if metric is not None:
            self._all_account_levels.append(
                self._safe_log_ratio(metric.all_account_long_short),
            )
            if prior is not None:
                self._all_account_changes.append(
                    self._safe_log_ratio(metric.all_account_long_short)
                    - self._safe_log_ratio(prior.all_account_long_short),
                )
                self._top_account_changes.append(
                    self._safe_log_ratio(metric.top_account_long_short)
                    - self._safe_log_ratio(prior.top_account_long_short),
                )
                self._top_position_changes.append(
                    self._safe_log_ratio(metric.top_position_long_short)
                    - self._safe_log_ratio(prior.top_position_long_short),
                )
        super()._ingest_metric_history(snapshot, metric)

    def _branch_reset(
        self,
        snapshot: PrimitiveSnapshot,
        wave: _Wave,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> ScenarioStep:
        transition = self._transition(
            wave,
            wave.state,
            "RESET",
            reason,
            snapshot.observation.close,
            {**self._context_details(wave), **dict(details or {})},
        )
        self._wave = None
        self._cooldown_until = snapshot.index + int(
            self.params.get("oidb_cooldown_bars", 2),
        )
        self._crowding_context.pop(wave.scenario_id, None)
        return ScenarioStep(transitions=(transition,))

    def _advance_counter_inventory(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric | None,
        wave: _Wave,
        context: _CrowdingContext,
    ) -> ScenarioStep:
        if snapshot.index <= wave.started_index:
            return ScenarioStep()
        if not bool(
            self.params.get("cirb_enable_counter_inventory_continuation", True),
        ):
            return self._branch_reset(
                snapshot,
                wave,
                "COUNTER_INVENTORY_BRANCH_DISABLED_BY_PREDECLARED_ABLATION",
            )

        obs = snapshot.observation
        prior_extreme = wave.extreme
        wave.extreme = (
            min(wave.extreme, obs.low)
            if wave.side == "SELL"
            else max(wave.extreme, obs.high)
        )
        elapsed = snapshot.index - wave.started_index
        if elapsed > int(self.params.get("cirb_counter_response_bars", 15)):
            return self._branch_reset(
                snapshot,
                wave,
                "COUNTER_INVENTORY_REBUILD_RESPONSE_EXPIRED",
                {"elapsed_bars": elapsed},
            )

        floor = float(self.params.get("oidb_response_flow_ratio", 0.05))
        reclaim = float(self.params.get("oidb_reclaim_close_location", 0.58))
        if wave.side == "SELL":
            invalid = (
                obs.close >= wave.event_mid
                and snapshot.flow_ratio >= floor
                and snapshot.close_location >= reclaim
            )
        else:
            invalid = (
                obs.close <= wave.event_mid
                and snapshot.flow_ratio <= -floor
                and snapshot.close_location <= 1.0 - reclaim
            )
        if invalid:
            return self._branch_reset(
                snapshot,
                wave,
                "COUNTER_INVENTORY_CONTINUATION_INVALIDATED_BY_OPPOSITE_RECLAIM",
            )
        if metric is None or metric.ts_ns <= context.event_ts_ns:
            return ScenarioStep()

        rebuild = (
            (metric.open_interest - context.event_open_interest)
            / context.event_open_interest
        )
        required_rebuild = wave.event_drop * float(
            self.params.get("cirb_counter_rebuild_fraction", 0.35),
        )
        composition_change = self._safe_log_ratio(
            metric.all_account_long_short / context.event_all_account_ratio,
        )
        composition_persists = (
            context.shock_sign * composition_change <= 0.0
            if bool(
                self.params.get(
                    "cirb_require_counter_composition_persistence",
                    True,
                ),
            )
            else True
        )
        extension = float(self.params.get("oidb_extension_atr", 0.05)) * wave.atr
        if wave.side == "SELL":
            price_extends = (
                obs.close <= prior_extreme - extension
                and snapshot.flow_ratio <= -floor
                and snapshot.close_location <= 1.0 - reclaim
            )
        else:
            price_extends = (
                obs.close >= prior_extreme + extension
                and snapshot.flow_ratio >= floor
                and snapshot.close_location >= reclaim
            )
        if rebuild < required_rebuild or not composition_persists or not price_extends:
            return ScenarioStep()

        step = self._signal_continuation(snapshot, wave)
        if step.signal is None:
            return step
        signal = replace(
            step.signal,
            details={
                **dict(step.signal.details),
                "counter_inventory_rebuild_fraction": rebuild,
                "required_counter_inventory_rebuild_fraction": required_rebuild,
                "counter_composition_change_log": composition_change,
                "counter_composition_persists": composition_persists,
            },
        )
        return ScenarioStep(transitions=step.transitions, signal=signal)

    def _signal_reversal(
        self,
        snapshot: PrimitiveSnapshot,
        wave: _Wave,
    ) -> ScenarioStep:
        step = super()._signal_reversal(snapshot, wave)
        signal = step.signal
        if signal is None:
            return step
        context = self._crowding_context.get(wave.scenario_id)
        branch = "MISSING" if context is None else context.branch
        family = "CIRB_D_R" if branch == "DISCHARGE" else "CIRB_LEGACY_R"
        transitions: list[ScenarioTransition] = []
        for index, transition in enumerate(step.transitions):
            if index == 0:
                transitions.append(
                    replace(
                        transition,
                        event_type="CROWDING_INVENTORY_RESPONSE_TRANSITION",
                        reason_code=(
                            "CROWD_DISCHARGE_EXHAUSTION_AND_OPPOSITE_RECLAIM_CONFIRMED"
                            if branch == "DISCHARGE"
                            else transition.reason_code
                        ),
                        details={
                            **dict(transition.details),
                            **self._context_details(wave),
                        },
                    ),
                )
            else:
                transitions.append(
                    replace(
                        transition,
                        event_type="CIRB_ENTRY_TRANSITION",
                        reason_code=(
                            "CROWD_DISCHARGE_REVERSAL_ENTRY_ARMED"
                            if branch == "DISCHARGE"
                            else transition.reason_code
                        ),
                        details={
                            **dict(transition.details),
                            **self._context_details(wave),
                        },
                    ),
                )
        return ScenarioStep(
            transitions=tuple(transitions),
            signal=replace(
                signal,
                family=family,
                details={
                    **dict(signal.details),
                    **self._context_details(wave),
                },
            ),
        )

    def _signal_continuation(
        self,
        snapshot: PrimitiveSnapshot,
        wave: _Wave,
    ) -> ScenarioStep:
        context = self._crowding_context.get(wave.scenario_id)
        branch = "MISSING" if context is None else context.branch
        step = super()._signal_continuation(snapshot, wave)
        signal = step.signal
        if signal is None:
            return step
        if branch == "DISCHARGE":
            family = "CIRB_D_C"
            context_reason = "CROWD_DISCHARGE_PERSISTED_WITH_PRICE_DISCOVERY"
            entry_reason = "CROWD_DISCHARGE_CONTINUATION_ENTRY_ARMED"
        elif branch == "COUNTER_INVENTORY":
            family = "CIRB_T_C"
            context_reason = "COUNTER_INVENTORY_REBUILT_AND_TRAPPED_BY_PRICE_EXTENSION"
            entry_reason = "TRAPPED_COUNTER_INVENTORY_CONTINUATION_ENTRY_ARMED"
        else:
            family = "CIRB_LEGACY_C"
            context_reason = step.transitions[0].reason_code
            entry_reason = step.transitions[1].reason_code
        transitions = (
            replace(
                step.transitions[0],
                event_type="CROWDING_INVENTORY_RESPONSE_TRANSITION",
                reason_code=context_reason,
                details={
                    **dict(step.transitions[0].details),
                    **self._context_details(wave),
                },
            ),
            replace(
                step.transitions[1],
                event_type="CIRB_ENTRY_TRANSITION",
                reason_code=entry_reason,
                details={
                    **dict(step.transitions[1].details),
                    **self._context_details(wave),
                },
            ),
        )
        return ScenarioStep(
            transitions=transitions,
            signal=replace(
                signal,
                family=family,
                details={
                    **dict(signal.details),
                    **self._context_details(wave),
                },
            ),
        )

    def _advance_wave(
        self,
        snapshot: PrimitiveSnapshot,
        metric: FuturesMetric | None,
    ) -> ScenarioStep:
        wave = self._wave
        assert wave is not None
        context = self._crowding_context.get(wave.scenario_id)
        if wave.state.endswith("SIGNALLED"):
            step = super()._advance_wave(snapshot, metric)
        elif context is None or context.branch == "LEGACY":
            step = super()._advance_wave(snapshot, metric)
        elif context.branch == "AMBIGUOUS":
            step = self._branch_reset(
                snapshot,
                wave,
                "AMBIGUOUS_ACCOUNT_COMPOSITION_AT_OI_SHOCK",
            )
        elif context.branch == "DISCHARGE":
            if bool(self.params.get("cirb_enable_discharge_response", True)):
                step = super()._advance_wave(snapshot, metric)
            else:
                step = self._branch_reset(
                    snapshot,
                    wave,
                    "DISCHARGE_BRANCH_DISABLED_BY_PREDECLARED_ABLATION",
                )
        else:
            step = self._advance_counter_inventory(
                snapshot,
                metric,
                wave,
                context,
            )
        if self._wave is None:
            self._crowding_context.pop(wave.scenario_id, None)
        return step

    def abort_active(
        self,
        snapshot: PrimitiveSnapshot,
        reason: str,
    ) -> ScenarioStep:
        wave = self._wave
        step = super().abort_active(snapshot, reason)
        if wave is not None:
            self._crowding_context.pop(wave.scenario_id, None)
        return step
