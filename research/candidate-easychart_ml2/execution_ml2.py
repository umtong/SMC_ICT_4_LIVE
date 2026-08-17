"""NautilusTrader binding for the EasyChart ML2 CatBoost meta-policy.

The inherited execution layer still owns one continuous account, one global
position, full-position entry/exit, fixed stop/target and approximately 3% NAV
risk at the stop. ML2 changes only candidate selection and arbitration.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from execution_ml1 import EasyChartML1Strategy, _ScoredPlan
from ml1_features import FEATURE_NAMES
from ml2_model import CatBoostProbabilityModel


@dataclass(frozen=True, slots=True)
class ML2RuntimeConfig:
    mode: str = "shadow"
    model_metadata: Path = Path(__file__).resolve().parent / "models" / "untrained.json"

    def __post_init__(self) -> None:
        if self.mode not in {"shadow", "select"}:
            raise ValueError("ML2 mode must be 'shadow' or 'select'")

    @property
    def model_path(self) -> Path:
        return self.model_metadata


_RUNTIME = ML2RuntimeConfig()


def configure_ml2_runtime(config: ML2RuntimeConfig) -> None:
    global _RUNTIME
    _RUNTIME = config


class EasyChartML2Strategy(EasyChartML1Strategy):
    """ML1 causal feature/candidate pipeline with nonlinear ML2 selection."""

    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.ml2_runtime = _RUNTIME
        if self.ml2_runtime.mode == "select":
            model = CatBoostProbabilityModel(self.ml2_runtime.model_metadata)
            model.assert_selectable()
            if tuple(model.feature_names) != tuple(FEATURE_NAMES):
                raise RuntimeError(
                    "ML2 model feature schema does not match runtime code; rebuild dataset and model",
                )
            configured_risk = float(self.config.risk_fraction)
            if not math.isclose(configured_risk, model.risk_fraction, rel_tol=0.0, abs_tol=1e-12):
                raise RuntimeError(
                    f"ML2 model was trained for risk_fraction={model.risk_fraction}, "
                    f"but runtime uses {configured_risk}",
                )
            self.ml_model = model
        self.ml_runtime = SimpleNamespace(
            mode=self.ml2_runtime.mode,
            model_path=self.ml2_runtime.model_metadata,
        )

    def _score_plan(self, instrument_id: Any, plan: Any) -> _ScoredPlan:
        scored = super()._score_plan(instrument_id, plan)
        decision = scored.decision
        self._record(
            "ml2_plan_utility",
            plan_id=plan.plan_id,
            instrument_id=str(instrument_id),
            model_id=self.ml_model.model_id,
            ml2_expected_log_growth=getattr(decision, "expected_log_growth", None),
            ml2_expected_net_r=decision.expected_net_r,
            ml2_target_probability=decision.target_probability,
            ml2_accepted=decision.accepted,
            ml2_reason=decision.reason,
        )
        return scored

    @staticmethod
    def _ml_rank(item: _ScoredPlan) -> tuple[Any, ...]:
        plan = item.plan
        expected_log = getattr(item.decision, "expected_log_growth", float("-inf"))
        return (
            -expected_log,
            -item.decision.expected_net_r,
            -item.decision.target_probability,
            plan.interaction_time_ns,
            -plan.higher_timeframe_minutes,
            plan.symbol,
            plan.plan_id,
        )

    @property
    def ml_diagnostics(self) -> dict[str, Any]:
        output = dict(super().ml_diagnostics)
        output["runtime"] = {
            "mode": self.ml2_runtime.mode,
            "model_metadata": str(self.ml2_runtime.model_metadata),
            "model_id": self.ml_model.model_id,
            "model_status": self.ml_model.status,
            "decision": "positive_expected_log_nav_growth_at_fixed_risk",
        }
        output["ml2"] = {
            "model_family": "CatBoostClassifier + disjoint Platt calibration",
            "risk_fraction_is_not_model_controlled": True,
            "simultaneous_arbitration": "highest_expected_log_growth",
        }
        return output


StrategyClass = EasyChartML2Strategy
