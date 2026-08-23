"""Causal value of a source-bound pending bracket.

Entry-before-invalidation, target/stop after fill, and no-fill cancellation
are different economic events and remain separate here. Owner probability
mixes an owner-conditioned price process with the observed background process;
unsupported owner mass is never silently converted into a certain stop.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable


FIXED_RISK_FRACTION = 0.03
DEFAULT_RISK_FRACTION = FIXED_RISK_FRACTION  # compatibility name, not tunable
NO_TRADE_UTILITY = 0.0


class RouteValueError(ValueError):
    """The immutable route-value contract was violated."""


class UtilityMode(str, Enum):
    EXPECTED_R = "EXPECTED_R"
    EXPECTED_CASH = "EXPECTED_CASH"
    EXPECTED_LOG_NAV = "EXPECTED_LOG_NAV"


class RouteAction(str, Enum):
    TRADE = "TRADE"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class RouteGeometry:
    source_identity_token: str
    symbol: str
    side: str
    decision_time_ns: int
    first_eligible_time_ns: int
    bar_interval_minutes: int
    current_price: float
    entry: float
    stop: float
    target: float
    owner_probability: float
    owner_aligned_drift_per_bar: float
    owner_variance_per_bar: float
    background_aligned_drift_per_bar: float
    background_variance_per_bar: float
    cost_fraction: float = 0.0
    roundtrip_cost_price: float | None = None

    def __post_init__(self) -> None:
        if not self.source_identity_token or not self.symbol:
            raise RouteValueError("source identity and symbol are required")
        side = str(self.side).upper()
        if side not in {"LONG", "SHORT"}:
            raise RouteValueError("side must be LONG or SHORT")
        object.__setattr__(self, "side", side)
        if self.decision_time_ns < 0:
            raise RouteValueError("decision time cannot be negative")
        if self.first_eligible_time_ns <= self.decision_time_ns:
            raise RouteValueError("entry cannot be eligible on its evidence bar")
        if self.bar_interval_minutes <= 0:
            raise RouteValueError("bar interval must be positive")
        for name in (
            "current_price", "entry", "stop", "target", "owner_probability",
            "owner_aligned_drift_per_bar", "owner_variance_per_bar",
            "background_aligned_drift_per_bar", "background_variance_per_bar",
            "cost_fraction",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise RouteValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if self.roundtrip_cost_price is not None:
            value = float(self.roundtrip_cost_price)
            if not math.isfinite(value):
                raise RouteValueError("roundtrip cost must be finite")
            object.__setattr__(self, "roundtrip_cost_price", value)
        if min(self.current_price, self.entry, self.stop, self.target) <= 0.0:
            raise RouteValueError("prices must be positive")
        if side == "LONG" and not self.stop < self.current_price < self.entry < self.target:
            raise RouteValueError("LONG pending route requires stop < current < entry < target")
        if side == "SHORT" and not self.target < self.entry < self.current_price < self.stop:
            raise RouteValueError("SHORT pending route requires target < entry < current < stop")
        if not 0.0 <= self.owner_probability <= 1.0:
            raise RouteValueError("owner probability must be in [0, 1]")
        if self.owner_variance_per_bar <= 0.0 or self.background_variance_per_bar <= 0.0:
            raise RouteValueError("price-process variances must be positive")
        if self.cost_fraction < 0.0 or (
            self.roundtrip_cost_price is not None and self.roundtrip_cost_price < 0.0
        ):
            raise RouteValueError("cost cannot be negative")
        if self.cost_fraction > 0.0 and self.roundtrip_cost_price not in (None, 0.0):
            raise RouteValueError("provide fractional or absolute cost, not both")

    @property
    def direction(self) -> float:
        return 1.0 if self.side == "LONG" else -1.0

    @property
    def entry_distance_from_current(self) -> float:
        return self.direction * (self.entry - self.current_price)

    @property
    def prefill_invalidation_distance(self) -> float:
        return self.direction * (self.current_price - self.stop)

    @property
    def stop_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def target_distance(self) -> float:
        return abs(self.target - self.entry)

    @property
    def gross_rr(self) -> float:
        return self.target_distance / self.stop_distance

    @property
    def cost_price(self) -> float:
        if self.roundtrip_cost_price is not None:
            return self.roundtrip_cost_price
        return self.entry * self.cost_fraction

    @property
    def cost_r(self) -> float:
        return self.cost_price / self.stop_distance

    @property
    def deterministic_key(self) -> tuple[object, ...]:
        return (
            self.decision_time_ns, self.symbol, self.source_identity_token,
            self.side, self.entry, self.stop, self.target,
        )


def target_first_passage_probability(
    *,
    target_distance: float,
    stop_distance: float,
    aligned_drift_per_bar: float,
    variance_per_bar: float,
) -> float:
    """Probability of +target before -stop for a Brownian price process."""
    values = (target_distance, stop_distance, aligned_drift_per_bar, variance_per_bar)
    if not all(math.isfinite(float(value)) for value in values):
        raise RouteValueError("first-passage inputs must be finite")
    if target_distance <= 0.0 or stop_distance <= 0.0:
        raise RouteValueError("boundary distances must be positive")
    if variance_per_bar < 0.0:
        raise RouteValueError("variance cannot be negative")
    if variance_per_bar == 0.0:
        if aligned_drift_per_bar > 0.0:
            return 1.0
        if aligned_drift_per_bar < 0.0:
            return 0.0
        raise RouteValueError("zero drift and zero variance never reaches a boundary")

    total = target_distance + stop_distance
    scaled_drift = 2.0 * aligned_drift_per_bar / variance_per_bar
    span_exponent = scaled_drift * total
    if abs(span_exponent) < 1e-8:
        return stop_distance / total
    if scaled_drift > 0.0:
        probability = (-math.expm1(-scaled_drift * stop_distance)) / (
            -math.expm1(-span_exponent)
        )
    else:
        q = -scaled_drift

        def log_expm1_positive(value: float) -> float:
            if value > 50.0:
                return value + math.log1p(-math.exp(-value))
            return math.log(math.expm1(value))

        log_probability = log_expm1_positive(q * stop_distance) - log_expm1_positive(
            q * total
        )
        probability = 0.0 if log_probability < -745.0 else math.exp(log_probability)
    return min(1.0, max(0.0, probability))


@dataclass(frozen=True, slots=True)
class RouteEvaluation:
    geometry: RouteGeometry
    eligible: bool
    exclusion_reason: str | None
    owner_fill_probability: float
    background_fill_probability: float
    owner_target_given_fill: float
    background_target_given_fill: float
    fill_probability: float
    cancel_probability: float
    target_probability: float
    stop_probability: float
    conditional_target_probability: float
    gross_rr: float
    cost_r: float
    net_win_r: float
    net_loss_r: float
    risk_fraction: float
    risk_cash: float
    expected_r: float
    expected_cash: float
    expected_log_nav: float
    utility_mode: UtilityMode
    utility: float
    assumptions: tuple[str, ...]


_ASSUMPTIONS = (
    "entry eligibility begins strictly after the completed evidence bar",
    "pre-fill invalidation or cancellation has zero payoff",
    "owner and background are alternative price processes, not duplicate samples",
    "drift is price per bar and variance is price squared per the same bar clock",
    "cost is separate from gross planned reward/risk",
    "three-percent stop risk is a fixed execution sizing rule",
)


def _component_probabilities(
    geometry: RouteGeometry, *, drift: float, variance: float,
) -> tuple[float, float]:
    fill = target_first_passage_probability(
        target_distance=geometry.entry_distance_from_current,
        stop_distance=geometry.prefill_invalidation_distance,
        aligned_drift_per_bar=drift,
        variance_per_bar=variance,
    )
    target_given_fill = target_first_passage_probability(
        target_distance=geometry.target_distance,
        stop_distance=geometry.stop_distance,
        aligned_drift_per_bar=drift,
        variance_per_bar=variance,
    )
    return fill, target_given_fill


def evaluate_route(
    geometry: RouteGeometry,
    *,
    current_nav: float,
    utility_mode: UtilityMode | str = UtilityMode.EXPECTED_R,
) -> RouteEvaluation:
    if not math.isfinite(current_nav) or current_nav <= 0.0:
        raise RouteValueError("current NAV must be finite and positive")
    try:
        mode = UtilityMode(utility_mode)
    except ValueError as exc:
        raise RouteValueError(f"unknown utility mode: {utility_mode}") from exc

    owner_fill, owner_target = _component_probabilities(
        geometry,
        drift=geometry.owner_aligned_drift_per_bar,
        variance=geometry.owner_variance_per_bar,
    )
    background_fill, background_target = _component_probabilities(
        geometry,
        drift=geometry.background_aligned_drift_per_bar,
        variance=geometry.background_variance_per_bar,
    )
    owner_weight = geometry.owner_probability
    background_weight = 1.0 - owner_weight
    target_probability = owner_weight * owner_fill * owner_target + (
        background_weight * background_fill * background_target
    )
    stop_probability = owner_weight * owner_fill * (1.0 - owner_target) + (
        background_weight * background_fill * (1.0 - background_target)
    )
    fill_probability = target_probability + stop_probability
    cancel_probability = max(0.0, 1.0 - fill_probability)
    conditional_target = target_probability / fill_probability if fill_probability > 0.0 else 0.0

    gross_rr = geometry.gross_rr
    cost_r = geometry.cost_r
    net_win_r = gross_rr - cost_r
    net_loss_r = 1.0 + cost_r
    risk_cash = current_nav * FIXED_RISK_FRACTION
    expected_r = target_probability * net_win_r - stop_probability * net_loss_r
    expected_cash = expected_r * risk_cash
    win_multiplier = 1.0 + FIXED_RISK_FRACTION * net_win_r
    loss_multiplier = 1.0 - FIXED_RISK_FRACTION * net_loss_r

    exclusion_reason: str | None = None
    if gross_rr < 1.0 - 1e-12:
        exclusion_reason = "gross_rr_below_one"
    elif net_win_r <= 0.0:
        exclusion_reason = "cost_consumes_target_payoff"
    elif loss_multiplier <= 0.0:
        exclusion_reason = "cost_adjusted_stop_exhausts_nav"
    expected_log_nav = (
        target_probability * math.log(win_multiplier)
        + stop_probability * math.log(loss_multiplier)
        if win_multiplier > 0.0 and loss_multiplier > 0.0
        else -float.fromhex("0x1.fffffffffffffp+1023")
    )
    raw_utility = {
        UtilityMode.EXPECTED_R: expected_r,
        UtilityMode.EXPECTED_CASH: expected_cash,
        UtilityMode.EXPECTED_LOG_NAV: expected_log_nav,
    }[mode]
    eligible = exclusion_reason is None
    return RouteEvaluation(
        geometry=geometry,
        eligible=eligible,
        exclusion_reason=exclusion_reason,
        owner_fill_probability=owner_fill,
        background_fill_probability=background_fill,
        owner_target_given_fill=owner_target,
        background_target_given_fill=background_target,
        fill_probability=fill_probability,
        cancel_probability=cancel_probability,
        target_probability=target_probability,
        stop_probability=stop_probability,
        conditional_target_probability=conditional_target,
        gross_rr=gross_rr,
        cost_r=cost_r,
        net_win_r=net_win_r,
        net_loss_r=net_loss_r,
        risk_fraction=FIXED_RISK_FRACTION,
        risk_cash=risk_cash,
        expected_r=expected_r,
        expected_cash=expected_cash,
        expected_log_nav=expected_log_nav,
        utility_mode=mode,
        utility=raw_utility if eligible else NO_TRADE_UTILITY,
        assumptions=_ASSUMPTIONS,
    )


@dataclass(frozen=True, slots=True)
class RouteSelection:
    action: RouteAction
    selected: RouteEvaluation | None
    evaluations: tuple[RouteEvaluation, ...]
    no_trade_utility: float
    utility_mode: UtilityMode
    assumptions: tuple[str, ...]


def select_best_route(
    candidates: Iterable[RouteGeometry],
    *,
    current_nav: float,
    utility_mode: UtilityMode | str = UtilityMode.EXPECTED_R,
) -> RouteSelection:
    try:
        mode = UtilityMode(utility_mode)
    except ValueError as exc:
        raise RouteValueError(f"unknown utility mode: {utility_mode}") from exc
    ordered = tuple(sorted(candidates, key=lambda item: item.deterministic_key))
    evaluations = tuple(
        evaluate_route(item, current_nav=current_nav, utility_mode=mode) for item in ordered
    )
    actionable = tuple(
        item for item in evaluations if item.eligible and item.utility > NO_TRADE_UTILITY
    )
    selected = max(actionable, key=lambda item: item.utility, default=None)
    return RouteSelection(
        action=RouteAction.TRADE if selected is not None else RouteAction.NO_TRADE,
        selected=selected,
        evaluations=evaluations,
        no_trade_utility=NO_TRADE_UTILITY,
        utility_mode=mode,
        assumptions=_ASSUMPTIONS,
    )


__all__ = [
    "DEFAULT_RISK_FRACTION", "FIXED_RISK_FRACTION", "NO_TRADE_UTILITY",
    "RouteAction", "RouteEvaluation", "RouteGeometry", "RouteSelection",
    "RouteValueError", "UtilityMode", "evaluate_route", "select_best_route",
    "target_first_passage_probability",
]
