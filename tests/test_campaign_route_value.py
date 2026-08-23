from __future__ import annotations

import math

import pytest

from smc_ict_4.campaign_policy.route_value import (
    FIXED_RISK_FRACTION,
    RouteAction,
    RouteGeometry,
    RouteValueError,
    UtilityMode,
    evaluate_route,
    select_best_route,
    target_first_passage_probability,
)


def _route(**overrides: float | str | int | None) -> RouteGeometry:
    values: dict[str, float | str | int | None] = {
        "source_identity_token": "btc-parent\x1f1\x1fLONG",
        "symbol": "BTCUSDT",
        "side": "LONG",
        "decision_time_ns": 100,
        "first_eligible_time_ns": 200,
        "bar_interval_minutes": 5,
        "current_price": 99.5,
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
        "owner_probability": 1.0,
        "owner_aligned_drift_per_bar": 0.0,
        "owner_variance_per_bar": 1.0,
        "background_aligned_drift_per_bar": 0.0,
        "background_variance_per_bar": 1.0,
        "cost_fraction": 0.0,
        "roundtrip_cost_price": None,
    }
    values.update(overrides)
    return RouteGeometry(**values)  # type: ignore[arg-type]


def test_zero_drift_matches_two_boundary_probability() -> None:
    probability = target_first_passage_probability(
        target_distance=2.0,
        stop_distance=1.0,
        aligned_drift_per_bar=0.0,
        variance_per_bar=3.0,
    )
    assert probability == pytest.approx(1.0 / 3.0)


def test_zero_drift_and_zero_variance_is_not_a_fabricated_geometric_path() -> None:
    with pytest.raises(RouteValueError, match="never reaches"):
        target_first_passage_probability(
            target_distance=2.0,
            stop_distance=1.0,
            aligned_drift_per_bar=0.0,
            variance_per_bar=0.0,
        )


def test_stronger_aligned_drift_increases_target_probability_stably() -> None:
    probabilities = [
        target_first_passage_probability(
            target_distance=2.0,
            stop_distance=1.0,
            aligned_drift_per_bar=drift,
            variance_per_bar=1.0,
        )
        for drift in (-1e9, -0.5, 0.0, 0.5, 1e9)
    ]
    assert probabilities == sorted(probabilities)
    assert probabilities[0] == 0.0 and probabilities[-1] == 1.0
    assert all(math.isfinite(value) for value in probabilities)


def test_no_fill_is_zero_payoff_not_a_stop_loss() -> None:
    evaluation = evaluate_route(_route(), current_nav=10_000.0)

    # From 99.5, entry 100 and pre-fill invalidation 99 are symmetric.
    assert evaluation.fill_probability == pytest.approx(0.5)
    assert evaluation.cancel_probability == pytest.approx(0.5)
    assert evaluation.target_probability == pytest.approx(1.0 / 6.0)
    assert evaluation.stop_probability == pytest.approx(1.0 / 3.0)
    assert evaluation.target_probability + evaluation.stop_probability + evaluation.cancel_probability == pytest.approx(1.0)


def test_owner_and_background_are_alternative_predictive_processes() -> None:
    low_owner = evaluate_route(
        _route(
            owner_probability=0.1,
            owner_aligned_drift_per_bar=1.0,
            background_aligned_drift_per_bar=-1.0,
        ),
        current_nav=10_000.0,
    )
    high_owner = evaluate_route(
        _route(
            owner_probability=0.9,
            owner_aligned_drift_per_bar=1.0,
            background_aligned_drift_per_bar=-1.0,
        ),
        current_nav=10_000.0,
    )

    assert high_owner.target_probability > low_owner.target_probability
    assert low_owner.cancel_probability < 1.0
    assert low_owner.stop_probability < 1.0


def test_cost_is_separate_from_gross_rr_and_reduces_value() -> None:
    free = evaluate_route(
        _route(owner_aligned_drift_per_bar=0.8), current_nav=10_000.0
    )
    costly = evaluate_route(
        _route(owner_aligned_drift_per_bar=0.8, roundtrip_cost_price=0.2),
        current_nav=10_000.0,
    )
    assert free.gross_rr == costly.gross_rr == 2.0
    assert costly.cost_r == pytest.approx(0.2)
    assert costly.expected_r < free.expected_r


def test_gross_rr_floor_is_domain_rule() -> None:
    evaluation = evaluate_route(
        _route(target=100.9, owner_aligned_drift_per_bar=10.0),
        current_nav=10_000.0,
    )
    assert evaluation.gross_rr == pytest.approx(0.9)
    assert not evaluation.eligible
    assert evaluation.exclusion_reason == "gross_rr_below_one"
    assert evaluation.utility == 0.0


def test_entry_cannot_be_eligible_on_completed_evidence_bar() -> None:
    with pytest.raises(RouteValueError, match="evidence bar"):
        _route(first_eligible_time_ns=100)


def test_global_selection_is_expected_r_and_ties_are_structurally_deterministic() -> None:
    weak = _route(
        source_identity_token="weak",
        symbol="ETHUSDT",
        owner_aligned_drift_per_bar=-1.0,
    )
    good = _route(
        source_identity_token="good",
        symbol="SOLUSDT",
        owner_aligned_drift_per_bar=2.0,
    )
    selection = select_best_route([weak, good], current_nav=10_000.0)
    assert selection.action is RouteAction.TRADE
    assert selection.utility_mode is UtilityMode.EXPECTED_R
    assert selection.selected is not None
    assert selection.selected.geometry.source_identity_token == "good"

    a = _route(source_identity_token="a", owner_aligned_drift_per_bar=0.5)
    b = _route(source_identity_token="b", owner_aligned_drift_per_bar=0.5)
    left = select_best_route([b, a], current_nav=10_000.0)
    right = select_best_route([a, b], current_nav=10_000.0)
    assert left.selected is not None and right.selected is not None
    assert left.selected.geometry.source_identity_token == "a"
    assert right.selected.geometry.source_identity_token == "a"


def test_three_percent_is_fixed_sizing_output_not_a_selection_parameter() -> None:
    evaluation = evaluate_route(
        _route(owner_aligned_drift_per_bar=0.8),
        current_nav=50_000.0,
        utility_mode=UtilityMode.EXPECTED_LOG_NAV,
    )
    assert evaluation.risk_fraction == FIXED_RISK_FRACTION
    assert evaluation.risk_cash == pytest.approx(1_500.0)
    assert evaluation.expected_log_nav == pytest.approx(
        evaluation.target_probability * math.log(1.0 + 0.03 * evaluation.net_win_r)
        + evaluation.stop_probability * math.log(1.0 - 0.03 * evaluation.net_loss_r)
    )
