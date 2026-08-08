"""Online selective approval for Candidate 11.

The gate never changes position size. It either approves the exact project risk
rate or abstains. Only terminal, chronologically observed trade outcomes may be
added to calibration history.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import log, sqrt
from typing import Deque


@dataclass(frozen=True, slots=True)
class CalibrationKey:
    scenario: str
    session_pair: str
    volatility_regime: str


@dataclass(frozen=True, slots=True)
class TerminalOutcome:
    sequence: int
    key: CalibrationKey
    won: bool
    net_r: float

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.net_r <= 0:
            raise ValueError("invalid terminal outcome")


@dataclass(frozen=True, slots=True)
class GateDecision:
    approved: bool
    reason: str
    sample_size: int
    win_rate: float | None
    win_probability_lower_bound: float | None
    log_growth_break_even_probability: float
    drift_detected: bool


class OnlineLogGrowthGate:
    def __init__(
        self,
        *,
        risk_fraction: float = 0.03,
        min_bucket_samples: int = 24,
        min_scenario_samples: int = 48,
        confidence_z: float = 1.96,
        probability_safety_margin: float = 0.04,
        drift_window: int = 16,
        max_history: int = 600,
    ) -> None:
        if not 0 < risk_fraction <= 0.03:
            raise ValueError("risk_fraction must be in (0, 0.03]")
        if min_bucket_samples < 5 or min_scenario_samples < min_bucket_samples:
            raise ValueError("invalid calibration sample requirements")
        if confidence_z <= 0 or not 0 <= probability_safety_margin < 0.5:
            raise ValueError("invalid confidence settings")
        if drift_window < 5:
            raise ValueError("drift_window must be at least five")
        self.risk_fraction = risk_fraction
        self.min_bucket_samples = min_bucket_samples
        self.min_scenario_samples = min_scenario_samples
        self.confidence_z = confidence_z
        self.probability_safety_margin = probability_safety_margin
        self.drift_window = drift_window
        self._history: Deque[TerminalOutcome] = deque(maxlen=max_history)
        self._last_sequence = -1

    @staticmethod
    def _wilson_lower(wins: int, n: int, z: float) -> float:
        if n <= 0:
            return 0.0
        p = wins / n
        z2 = z * z
        center = p + z2 / (2 * n)
        radius = z * sqrt((p * (1 - p) + z2 / (4 * n)) / n)
        return max(0.0, (center - radius) / (1 + z2 / n))

    def observe(self, outcome: TerminalOutcome) -> None:
        if outcome.sequence <= self._last_sequence:
            raise ValueError("terminal outcomes must be observed in strict sequence order")
        self._last_sequence = outcome.sequence
        self._history.append(outcome)

    def _sample(self, key: CalibrationKey) -> tuple[list[TerminalOutcome], str]:
        exact = [x for x in self._history if x.key == key]
        if len(exact) >= self.min_bucket_samples:
            return exact, "EXACT_CAUSAL_BUCKET"
        scenario = [x for x in self._history if x.key.scenario == key.scenario]
        if len(scenario) >= self.min_scenario_samples:
            return scenario, "SCENARIO_HIERARCHICAL_FALLBACK"
        return scenario, "INSUFFICIENT_CAUSAL_CALIBRATION"

    def _drift(self, sample: list[TerminalOutcome]) -> bool:
        w = self.drift_window
        if len(sample) < 2 * w:
            return False
        ordered = sorted(sample, key=lambda x: x.sequence)
        recent = ordered[-w:]
        reference = ordered[-2 * w:-w]
        pr = sum(x.won for x in recent) / w
        p0 = sum(x.won for x in reference) / w
        pooled = sum(x.won for x in recent + reference) / (2 * w)
        standard = sqrt(max(1e-12, pooled * (1 - pooled) * (2 / w)))
        return pr < p0 - 1.96 * standard

    def decide(self, key: CalibrationKey, *, net_r: float) -> GateDecision:
        if net_r <= 0:
            raise ValueError("net_r must be positive")
        rho = self.risk_fraction
        gain_log = log(1 + rho * net_r)
        loss_log = log(1 - rho)
        break_even = -loss_log / (gain_log - loss_log)
        sample, source = self._sample(key)
        if source == "INSUFFICIENT_CAUSAL_CALIBRATION":
            return GateDecision(False, source, len(sample), None, None, break_even, False)
        drift = self._drift(sample)
        wins = sum(x.won for x in sample)
        n = len(sample)
        win_rate = wins / n
        lower = self._wilson_lower(wins, n, self.confidence_z)
        if drift:
            return GateDecision(False, "ONLINE_ERROR_RATE_CHANGE", n, win_rate, lower, break_even, True)
        threshold = min(0.999999, break_even + self.probability_safety_margin)
        if lower <= threshold:
            return GateDecision(False, "LOG_GROWTH_LOWER_BOUND_NOT_POSITIVE", n, win_rate, lower, break_even, False)
        return GateDecision(True, source, n, win_rate, lower, break_even, False)
