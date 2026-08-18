from __future__ import annotations

from dataclasses import dataclass

from domain import Side
from features_integrated import (
    FEATURE_CLIP_RANGES,
    FEATURE_DEFAULTS,
    FEATURE_NAMES,
    integrated_context_features,
    plan_integrated_context_features,
)
from integrated_ensemble import ENSEMBLE_SCHEMA, IntegratedPeriodEnsemble
from ml1_model import TradeEconomics


@dataclass
class _Plan:
    side: Side = Side.LONG
    entry: float = 100.0
    stop: float = 99.0
    family: str = "INTEGRATED"
    scenario_path: str = "REJECTION"
    scale_name: str = "15m"
    rule_provenance: tuple[str, ...] = (
        "RESEARCH_STATE:STRUCTURE_60=0.75",
        "RESEARCH_STATE:STRUCTURE_15=0.50",
        "RESEARCH_STATE:CHANNEL_CONFLUENCE=0.40",
        "RESEARCH_STATE:CAUSAL_NOISE_BUFFER=0.20",
    )


def _document(probability_intercept: float = 2.0):
    members = []
    for index in range(3):
        members.append(
            {
                "member_id": f"m{index}",
                "calibration_window": f"p{index}",
                "feature_names": list(FEATURE_NAMES),
                "centers": [0.0] * len(FEATURE_NAMES),
                "scales": [1.0] * len(FEATURE_NAMES),
                "coefficients": [0.0] * len(FEATURE_NAMES),
                "intercept": probability_intercept,
            }
        )
    return IntegratedPeriodEnsemble.finalize_document(
        {
            "schema": ENSEMBLE_SCHEMA,
            "status": "trained",
            "feature_names": list(FEATURE_NAMES),
            "feature_defaults": dict(FEATURE_DEFAULTS),
            "feature_clip_ranges": {
                key: list(value) for key, value in FEATURE_CLIP_RANGES.items()
            },
            "aggregation": {
                "probability_quantile": 0.25,
                "duration_floor_minutes": 1.0,
            },
            "members": members,
            "duration_priors": {
                "exact": {},
                "state": {},
                "family": {},
                "global": 60.0,
            },
        }
    )


def test_integrated_context_is_side_relative() -> None:
    long = integrated_context_features(
        side=Side.LONG,
        entry=100.0,
        stop=99.0,
        structure_60=0.8,
        structure_15=-0.2,
        channel_confluence=0.5,
        causal_noise_buffer=0.25,
    )
    short = integrated_context_features(
        side=Side.SHORT,
        entry=100.0,
        stop=101.0,
        structure_60=0.8,
        structure_15=-0.2,
        channel_confluence=0.5,
        causal_noise_buffer=0.25,
    )
    assert long["integrated_structure_60_side"] == 0.8
    assert short["integrated_structure_60_side"] == -0.8
    assert long["integrated_structure_agreement"] == -1.0
    assert long["integrated_channel_x_min_alignment"] < 0.0


def test_plan_provenance_reconstructs_runtime_context() -> None:
    features = plan_integrated_context_features(_Plan())
    assert features["integrated_available"] == 1.0
    assert features["integrated_structure_60_side"] == 0.75
    assert features["integrated_structure_15_side"] == 0.50
    assert features["integrated_channel_confluence"] == 0.40


def test_missing_provenance_is_explicitly_unavailable() -> None:
    plan = _Plan(rule_provenance=())
    features = plan_integrated_context_features(plan)
    assert features["integrated_available"] == 0.0
    assert features["integrated_structure_60_raw"] == 0.0


def test_portable_integrated_ensemble_is_deterministic() -> None:
    model = IntegratedPeriodEnsemble(_document())
    model.assert_selectable()
    features = dict(FEATURE_DEFAULTS)
    first = model.probabilities(features)
    second = model.probabilities(features)
    assert first == second
    assert len(first) == 3
    assert all(0.5 < value < 1.0 for value in first)


def test_positive_after_cost_growth_is_selected() -> None:
    model = IntegratedPeriodEnsemble(_document(2.5))
    economics = TradeEconomics(
        win_net_r=1.4,
        loss_net_r=-1.0,
        break_even_probability=1.0 / 2.4,
    )
    score = model.score(
        dict(FEATURE_DEFAULTS),
        economics,
        _Plan(),
        risk_fraction=0.03,
    )
    assert score.accepted
    assert score.expected_log_growth > 0.0
    assert score.expected_account_r > 0.0
