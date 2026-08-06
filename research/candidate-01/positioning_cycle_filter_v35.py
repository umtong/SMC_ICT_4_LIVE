"""Causal OI build-and-release confirmation for the frozen v23 scenario.

V23 already detects a failed 40-bp intrinsic liquidity sweep, aligned-flow MSS,
calendar external-liquidity destination and full sweep-to-MSS invalidation.
V35 changes one variable only: a v23 plan is executable when official open
interest first expanded into the swept-direction initiative leg and then
contracted by the completed MSS. This represents position creation followed by
trapped-position release, rather than another price-pattern threshold.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from impact_regime_probe import ScenarioPlan
from positioning_metrics_data_v35 import PositionMetric, PositionMetricBook


PRIMARY_SUFFIX = ":oi-build-release-primary"
CONTROL_SUFFIX = ":v23-mss-control"


@dataclass(frozen=True, slots=True)
class PositioningCycleDiagnostic:
    source_scenario_id: str
    source_plan_id: str
    primary_plan_id: str | None
    control_plan_id: str
    side: str
    armed_time_ns: int | None
    sweep_event_time_ns: int | None
    signal_time_ns: int
    prior_observation_time_ns: int | None
    prior_available_time_ns: int | None
    prior_open_interest: float | None
    sweep_observation_time_ns: int | None
    sweep_available_time_ns: int | None
    sweep_open_interest: float | None
    mss_observation_time_ns: int | None
    mss_available_time_ns: int | None
    mss_open_interest: float | None
    open_interest_build: float | None
    open_interest_release: float | None
    later_metric_observed_by_mss: bool
    positioning_cycle_confirmed: bool
    reason_code: str


def source_scenario_id(plan: ScenarioPlan) -> str:
    marker = ":mss-displacement:"
    if marker not in plan.scenario_id:
        raise ValueError(f"not a frozen v23 MSS-displacement plan: {plan.scenario_id}")
    return plan.scenario_id.split(marker, 1)[0]


def _metric_fields(
    row: PositionMetric | None,
) -> tuple[int | None, int | None, float | None]:
    if row is None:
        return None, None, None
    return (
        int(row.observation_time_ns),
        int(row.available_time_ns),
        float(row.sum_open_interest),
    )


def build_positioning_cycle_plans(
    *,
    source_plans: Iterable[ScenarioPlan],
    transitions: Iterable[Any],
    sweep_times: Mapping[str, int],
    metrics: PositionMetricBook,
) -> tuple[
    list[ScenarioPlan],
    list[ScenarioPlan],
    list[PositioningCycleDiagnostic],
    Counter[str],
]:
    armed_times: dict[str, int] = {}
    for transition in transitions:
        if str(transition.event_type) != "ARMED":
            continue
        scenario_id = str(transition.scenario_id)
        observed = int(transition.event_time_ns)
        if scenario_id in armed_times and armed_times[scenario_id] != observed:
            raise RuntimeError(f"duplicate v23 arm transition: {scenario_id}")
        armed_times[scenario_id] = observed

    primary: list[ScenarioPlan] = []
    control: list[ScenarioPlan] = []
    diagnostics: list[PositioningCycleDiagnostic] = []
    counts: Counter[str] = Counter()

    for source in sorted(
        source_plans,
        key=lambda row: (row.signal_time_ns, row.scenario_id),
    ):
        base = source_scenario_id(source)
        control_plan = replace(
            source,
            scenario_id=source.scenario_id + CONTROL_SUFFIX,
            reason_code=(
                "FAILED_SWEEP_ALIGNED_FLOW_MSS_TO_CALENDAR_LIQUIDITY_V23_CONTROL"
            ),
        )
        control.append(control_plan)
        armed_time = armed_times.get(base)
        sweep_event_time = sweep_times.get(base)

        prior_row: PositionMetric | None = None
        sweep_row: PositionMetric | None = None
        mss_row: PositionMetric | None = None
        build: float | None = None
        release: float | None = None
        later = False
        confirmed = False
        primary_id: str | None = None

        if armed_time is None:
            reason = "SOURCE_ARM_TRANSITION_NOT_FOUND"
        elif sweep_event_time is None:
            reason = "SOURCE_SWEEP_PIVOT_TIME_NOT_FOUND"
        elif sweep_event_time > armed_time:
            reason = "SOURCE_SWEEP_AFTER_CONFIRMATION_INVALID"
        else:
            sweep_index = metrics.index_at(sweep_event_time)
            mss_index = metrics.index_at(int(source.signal_time_ns))
            if sweep_index is None or sweep_index <= 0:
                reason = "INSUFFICIENT_CAUSAL_METRICS_BEFORE_SWEEP"
            elif mss_index is None:
                reason = "NO_CAUSAL_METRIC_BY_MSS"
            else:
                prior_row = metrics.rows[sweep_index - 1]
                sweep_row = metrics.rows[sweep_index]
                mss_row = metrics.rows[mss_index]
                build = (
                    float(sweep_row.sum_open_interest)
                    - float(prior_row.sum_open_interest)
                )
                release = (
                    float(mss_row.sum_open_interest)
                    - float(sweep_row.sum_open_interest)
                )
                later = mss_index > sweep_index
                if not later:
                    reason = "NO_LATER_COMPLETED_POSITION_METRIC_BY_MSS"
                elif build <= 0.0:
                    reason = "OPEN_INTEREST_DID_NOT_EXPAND_INTO_SWEEP"
                elif release >= 0.0:
                    reason = "OPEN_INTEREST_DID_NOT_CONTRACT_BY_MSS"
                else:
                    reason = "OPEN_INTEREST_BUILD_AND_RELEASE_CONFIRMED"
                    confirmed = True

        if confirmed:
            primary_plan = replace(
                source,
                scenario_id=source.scenario_id + PRIMARY_SUFFIX,
                reason_code=(
                    "FAILED_SWEEP_POSITION_BUILD_RELEASE_ALIGNED_FLOW_MSS_TO_"
                    "CALENDAR_EXTERNAL_LIQUIDITY"
                ),
            )
            primary.append(primary_plan)
            primary_id = primary_plan.scenario_id

        prior_observation, prior_available, prior_oi = _metric_fields(prior_row)
        sweep_observation, sweep_available, sweep_oi = _metric_fields(sweep_row)
        mss_observation, mss_available, mss_oi = _metric_fields(mss_row)
        diagnostics.append(
            PositioningCycleDiagnostic(
                source_scenario_id=base,
                source_plan_id=source.scenario_id,
                primary_plan_id=primary_id,
                control_plan_id=control_plan.scenario_id,
                side=source.side.value,
                armed_time_ns=armed_time,
                sweep_event_time_ns=sweep_event_time,
                signal_time_ns=int(source.signal_time_ns),
                prior_observation_time_ns=prior_observation,
                prior_available_time_ns=prior_available,
                prior_open_interest=prior_oi,
                sweep_observation_time_ns=sweep_observation,
                sweep_available_time_ns=sweep_available,
                sweep_open_interest=sweep_oi,
                mss_observation_time_ns=mss_observation,
                mss_available_time_ns=mss_available,
                mss_open_interest=mss_oi,
                open_interest_build=build,
                open_interest_release=release,
                later_metric_observed_by_mss=later,
                positioning_cycle_confirmed=confirmed,
                reason_code=reason,
            ),
        )
        counts[reason] += 1

    if len(control) != len(diagnostics):
        raise RuntimeError("every v23 control plan requires one positioning diagnostic")
    if len(primary) != sum(row.positioning_cycle_confirmed for row in diagnostics):
        raise RuntimeError("v35 primary/diagnostic count mismatch")
    return primary, control, diagnostics, counts


__all__ = [
    "CONTROL_SUFFIX",
    "PRIMARY_SUFFIX",
    "PositioningCycleDiagnostic",
    "build_positioning_cycle_plans",
    "source_scenario_id",
]
