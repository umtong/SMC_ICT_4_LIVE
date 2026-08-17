from __future__ import annotations

from types import SimpleNamespace

from ml1_model import MODEL_SCHEMA, PortableBinaryModel, TradeEconomics
from robust_ensemble import ENSEMBLE_SCHEMA, PeriodRobustEnsemble


def constant(probability: float, name: str, feature_names: list[str] | None = None) -> dict:
    names = [] if feature_names is None else feature_names
    document = {
        "schema": MODEL_SCHEMA,
        "model_type": "constant_probability",
        "status": "trained",
        "model_id": name,
        "feature_names": names,
        "feature_defaults": {item: 0.0 for item in names},
        "feature_clip_ranges": {item: [-1.0, 1.0] for item in names},
        "constant_probability": probability,
        "calibration": {"kind": "identity"},
        "decision": {},
    }
    document["model_id"] = PortableBinaryModel.stable_id(document)
    return document


def ensemble(probabilities: list[float]) -> PeriodRobustEnsemble:
    document = PeriodRobustEnsemble.finalize_document(
        {
            "schema": ENSEMBLE_SCHEMA,
            "status": "trained",
            "ensemble_id": "pending",
            "feature_names": [],
            "members": [
                {
                    "member_id": f"m{index}",
                    "calibration_window": f"w{index}",
                    "model": constant(value, f"m{index}"),
                }
                for index, value in enumerate(probabilities)
            ],
            "duration_priors": {
                "exact": {"F|ACCEPTANCE|S": 30.0},
                "state": {},
                "family": {},
                "global": 90.0,
            },
            "aggregation": {
                "probability_quantile": 0.25,
                "duration_floor_minutes": 1.0,
            },
            "training": {},
        }
    )
    return PeriodRobustEnsemble(document)


def economics() -> TradeEconomics:
    return TradeEconomics(
        planned_risk=1.0,
        planned_reward=2.0,
        gross_rr=2.0,
        win_net_r=1.90,
        loss_net_r=-1.05,
        break_even_probability=1.05 / (1.90 + 1.05),
        entry_fill=100.0,
        target_fill=102.0,
        stop_fill=99.0,
        estimated_win_cost_r=0.10,
        estimated_loss_cost_r=0.05,
    )


def test_probability_below_half_can_trade_when_log_growth_is_positive() -> None:
    model = ensemble([0.30, 0.60, 0.70])
    plan = SimpleNamespace(family="F", scenario_path="ACCEPTANCE", scale_name="S")
    score = model.score({}, economics(), plan, risk_fraction=0.03)
    assert score.robust_target_probability == 0.45
    assert score.robust_target_probability < 0.5
    assert score.accepted
    assert score.expected_log_growth > 0.0
    assert score.expected_duration_minutes == 30.0


def test_period_disagreement_can_reject_a_positive_center() -> None:
    model = ensemble([0.10, 0.55, 0.70])
    plan = SimpleNamespace(family="F", scenario_path="ACCEPTANCE", scale_name="S")
    score = model.score({}, economics(), plan, risk_fraction=0.03)
    assert score.probability_median == 0.55
    assert score.robust_target_probability < score.probability_median
    assert not score.accepted
    assert score.reason == "NONPOSITIVE_ROBUST_LOG_GROWTH"


def test_feature_schema_must_match_across_members() -> None:
    document = PeriodRobustEnsemble.finalize_document(
        {
            "schema": ENSEMBLE_SCHEMA,
            "status": "trained",
            "ensemble_id": "pending",
            "members": [
                {"member_id": "a", "calibration_window": "a", "model": constant(0.5, "a")},
                {"member_id": "b", "calibration_window": "b", "model": constant(0.5, "b")},
                {"member_id": "c", "calibration_window": "c", "model": constant(0.5, "c", ["x"])},
            ],
            "duration_priors": {"global": 60.0},
            "aggregation": {"probability_quantile": 0.25},
        }
    )
    try:
        PeriodRobustEnsemble(document)
    except ValueError as exc:
        assert "feature schema" in str(exc)
    else:
        raise AssertionError("mismatched member schemas must fail loudly")
