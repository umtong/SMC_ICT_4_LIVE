"""Causal context gate for Candidate 57's crowded-long breakdown family.

This module is deliberately independent of NautilusTrader. It solves only the
candidate-specific problem: read the verified feature stream at or before an
auction episode, reject stale/future observations, and arbitrate all four
symbols after applying the context gate.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, TypeVar

import numpy as np
import pandas as pd

SYMBOL_PRIORITY: Mapping[str, int] = {
    "BTCUSDT": 0,
    "ETHUSDT": 1,
    "SOLUSDT": 2,
    "XRPUSDT": 3,
}


@dataclass(frozen=True, slots=True)
class CrowdGateConfig:
    crowd_min_ratio: float = 1.20
    taker_max_ratio: float = 1.00
    max_row_age_seconds: float = 65.0
    max_metrics_age_seconds: float = 305.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.crowd_min_ratio) or self.crowd_min_ratio <= 0.0:
            raise ValueError("crowd_min_ratio must be finite and positive")
        if not math.isfinite(self.taker_max_ratio) or self.taker_max_ratio <= 0.0:
            raise ValueError("taker_max_ratio must be finite and positive")
        if self.max_row_age_seconds < 0.0 or self.max_metrics_age_seconds < 0.0:
            raise ValueError("age limits must be non-negative")


@dataclass(frozen=True, slots=True)
class CrowdObservation:
    row_observed_time_ns: int
    metrics_observed_time_ns: int
    metrics_age_seconds: float
    crowd_ratio: float
    taker_ratio: float
    ready: bool
    reason: str


@dataclass(frozen=True, slots=True)
class GateAssessment:
    passed: bool
    reason: str
    confirmation_mode: str | None
    observation: CrowdObservation


class CrowdGateStore:
    REQUIRED_COLUMNS = (
        "observed_time_ns",
        "metrics_observed_time_ns",
        "metrics_age_seconds",
        "metrics_ready",
        "sum_toptrader_long_short_ratio",
        "sum_taker_long_short_vol_ratio",
    )

    def __init__(self, feature_path: Path | str) -> None:
        self.path = Path(feature_path)
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        frame = pd.read_csv(
            self.path,
            compression="infer",
            usecols=list(self.REQUIRED_COLUMNS),
        )
        if frame.empty:
            raise ValueError(f"empty crowd feature stream: {self.path}")

        row_times = pd.to_numeric(
            frame["observed_time_ns"], errors="raise"
        ).astype("int64")
        values = row_times.to_numpy(copy=True)
        if np.any(np.diff(values) <= 0):
            raise ValueError(
                f"crowd feature times must be unique and increasing: {self.path}"
            )

        self.row_times = values
        self.metrics_times = pd.to_numeric(
            frame["metrics_observed_time_ns"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.metrics_ages = pd.to_numeric(
            frame["metrics_age_seconds"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.crowd_ratios = pd.to_numeric(
            frame["sum_toptrader_long_short_ratio"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.taker_ratios = pd.to_numeric(
            frame["sum_taker_long_short_vol_ratio"], errors="coerce"
        ).to_numpy(dtype=np.float64, copy=True)
        self.metrics_ready = (
            frame["metrics_ready"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes"})
            .to_numpy(dtype=np.bool_, copy=True)
        )

    def observation(
        self,
        decision_time_ns: int,
        config: CrowdGateConfig = CrowdGateConfig(),
    ) -> CrowdObservation:
        ts = int(decision_time_ns)
        index = int(np.searchsorted(self.row_times, ts, side="right") - 1)
        if index < 0:
            return CrowdObservation(
                0, 0, math.inf, math.nan, math.nan, False, "NO_PRIOR_ROW"
            )

        row_time = int(self.row_times[index])
        row_age = (ts - row_time) / 1_000_000_000.0
        if row_age < -1e-9:
            raise RuntimeError("future crowd feature row reached Candidate 57")
        if row_age > config.max_row_age_seconds:
            return CrowdObservation(
                row_time, 0, math.inf, math.nan, math.nan, False,
                "STALE_FEATURE_ROW",
            )

        metrics_time_raw = float(self.metrics_times[index])
        metrics_age = float(self.metrics_ages[index])
        crowd = float(self.crowd_ratios[index])
        taker = float(self.taker_ratios[index])
        if not bool(self.metrics_ready[index]):
            return CrowdObservation(
                row_time, 0, metrics_age, crowd, taker, False,
                "METRICS_NOT_READY",
            )
        if not all(
            math.isfinite(value)
            for value in (metrics_time_raw, metrics_age, crowd, taker)
        ):
            return CrowdObservation(
                row_time, 0, metrics_age, crowd, taker, False,
                "NONFINITE_METRICS",
            )

        metrics_time = int(metrics_time_raw)
        if metrics_time > ts:
            return CrowdObservation(
                row_time, metrics_time, metrics_age, crowd, taker, False,
                "FUTURE_METRICS_REJECTED",
            )
        timestamp_age = max(0.0, (ts - metrics_time) / 1_000_000_000.0)
        effective_age = max(metrics_age, timestamp_age)
        if effective_age > config.max_metrics_age_seconds:
            return CrowdObservation(
                row_time, metrics_time, effective_age, crowd, taker, False,
                "STALE_METRICS",
            )
        return CrowdObservation(
            row_time,
            metrics_time,
            effective_age,
            crowd,
            taker,
            True,
            "READY",
        )


def assess_gate(
    observation: CrowdObservation,
    *,
    used_di_component: bool,
    used_bb_component: bool,
    config: CrowdGateConfig = CrowdGateConfig(),
) -> GateAssessment:
    if not observation.ready:
        return GateAssessment(False, observation.reason, None, observation)
    if not observation.crowd_ratio > config.crowd_min_ratio:
        return GateAssessment(False, "CROWD_CONTEXT_ABSENT", None, observation)

    taker_sell = observation.taker_ratio < config.taker_max_ratio
    dual_component = bool(used_di_component and used_bb_component)
    if taker_sell:
        return GateAssessment(True, "PASS", "TAKER_SELL_DOMINANCE", observation)
    if dual_component:
        return GateAssessment(True, "PASS", "DI_AND_BB_CONCURRENCE", observation)
    return GateAssessment(False, "BREAKDOWN_UNCONFIRMED", None, observation)


class DecisionLike(Protocol):
    symbol: str
    state: str
    side: int
    score: float
    entry_reference: float
    stop_reference: float
    objective_reference: float
    episode_ts: int
    reasons: tuple[str, ...]
    diagnostics: Mapping[str, Any]

    @property
    def actionable(self) -> bool: ...


D = TypeVar("D", bound=DecisionLike)


def _rejected_decision(decision: D, assessment: GateAssessment) -> D:
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            "candidate57_crowd_gate_pass": 0,
            "candidate57_crowd_gate_reason": assessment.reason,
            "candidate57_crowd_ratio": assessment.observation.crowd_ratio,
            "candidate57_taker_ratio": assessment.observation.taker_ratio,
            "candidate57_metrics_age_seconds": (
                assessment.observation.metrics_age_seconds
            ),
            "candidate57_metrics_observed_time_ns": (
                assessment.observation.metrics_observed_time_ns
            ),
        }
    )
    return replace(
        decision,
        state="UNRESOLVED",
        side=0,
        score=0.0,
        entry_reference=math.nan,
        stop_reference=math.nan,
        objective_reference=math.nan,
        reasons=(assessment.reason,),
        diagnostics=diagnostics,
    )


def filter_and_rank(
    decisions: Mapping[str, D],
    stores: Mapping[str, CrowdGateStore],
    *,
    config: CrowdGateConfig = CrowdGateConfig(),
    symbol_priority: Mapping[str, int] = SYMBOL_PRIORITY,
) -> tuple[D | None, dict[str, D], dict[str, GateAssessment]]:
    filtered: dict[str, D] = {}
    assessments: dict[str, GateAssessment] = {}
    for symbol, decision in decisions.items():
        if not decision.actionable:
            filtered[symbol] = decision
            continue
        store = stores.get(symbol)
        if store is None:
            observation = CrowdObservation(
                0, 0, math.inf, math.nan, math.nan, False,
                "MISSING_CROWD_STORE",
            )
        else:
            observation = store.observation(int(decision.episode_ts), config)
        diagnostics = decision.diagnostics
        assessment = assess_gate(
            observation,
            used_di_component=bool(
                int(diagnostics.get("used_di_component", 0))
            ),
            used_bb_component=bool(
                int(diagnostics.get("used_bb_component", 0))
            ),
            config=config,
        )
        assessments[symbol] = assessment
        if assessment.passed:
            enriched = dict(diagnostics)
            enriched.update(
                {
                    "candidate57_crowd_gate_pass": 1,
                    "candidate57_crowd_gate_reason": "PASS",
                    "candidate57_confirmation_mode": assessment.confirmation_mode,
                    "candidate57_crowd_ratio": observation.crowd_ratio,
                    "candidate57_taker_ratio": observation.taker_ratio,
                    "candidate57_metrics_age_seconds": (
                        observation.metrics_age_seconds
                    ),
                    "candidate57_metrics_observed_time_ns": (
                        observation.metrics_observed_time_ns
                    ),
                }
            )
            filtered[symbol] = replace(decision, diagnostics=enriched)
        else:
            filtered[symbol] = _rejected_decision(decision, assessment)

    actionable = [item for item in filtered.values() if item.actionable]
    actionable.sort(
        key=lambda item: (
            -float(item.score),
            symbol_priority.get(item.symbol, 99),
            int(item.episode_ts),
        )
    )
    return (actionable[0] if actionable else None), filtered, assessments


__all__ = [
    "CrowdGateConfig",
    "CrowdGateStore",
    "CrowdObservation",
    "GateAssessment",
    "SYMBOL_PRIORITY",
    "assess_gate",
    "filter_and_rank",
]
