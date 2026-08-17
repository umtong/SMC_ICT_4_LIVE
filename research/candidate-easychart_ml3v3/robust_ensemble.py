"""Period-robust probability ensemble and account-growth arbitration for ML3v3."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from ml1_model import PortableBinaryModel, TradeEconomics


ENSEMBLE_SCHEMA = "easychart_ml3v3_period_robust_ensemble_v1"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(float(item) for item in values)
    if not ordered:
        raise ValueError("cannot take a quantile of an empty sequence")
    p = min(1.0, max(0.0, float(probability)))
    if len(ordered) == 1:
        return ordered[0]
    location = p * (len(ordered) - 1)
    lower = int(math.floor(location))
    upper = int(math.ceil(location))
    weight = location - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _stable_id(document: Mapping[str, Any]) -> str:
    payload = dict(document)
    payload.pop("ensemble_id", None)
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class RobustScore:
    member_probabilities: tuple[float, ...]
    probability_median: float
    probability_lower_quartile: float
    probability_upper_quartile: float
    probability_mad: float
    robust_target_probability: float
    target_account_r: float
    expected_account_r: float
    expected_log_growth: float
    expected_duration_minutes: float
    expected_log_growth_per_hour: float
    duration_source: str
    accepted: bool
    reason: str


class PeriodRobustEnsemble:
    """Aggregate independently calibrated development-period models conservatively."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = dict(document)
        if self.document.get("schema") != ENSEMBLE_SCHEMA:
            raise ValueError(
                f"unsupported ensemble schema {self.document.get('schema')!r}"
            )
        self.status = str(self.document.get("status", "unknown"))
        self.ensemble_id = str(self.document.get("ensemble_id", "unidentified"))
        aggregation = dict(self.document.get("aggregation", {}))
        self.probability_quantile = _finite(
            aggregation.get("probability_quantile", 0.25),
            0.25,
        )
        self.duration_floor_minutes = max(
            1.0,
            _finite(aggregation.get("duration_floor_minutes", 1.0), 1.0),
        )
        if not 0.0 <= self.probability_quantile <= 0.5:
            raise ValueError("robust probability quantile must be within [0, 0.5]")

        raw_members = self.document.get("members")
        if not isinstance(raw_members, list) or len(raw_members) < 3:
            raise ValueError("period-robust ensemble requires at least three members")
        self.member_ids: tuple[str, ...] = tuple(
            str(item.get("member_id", f"member-{index}"))
            for index, item in enumerate(raw_members)
        )
        self.member_windows: tuple[str, ...] = tuple(
            str(item.get("calibration_window", "unknown"))
            for item in raw_members
        )
        self.members: tuple[PortableBinaryModel, ...] = tuple(
            PortableBinaryModel(item["model"])
            for item in raw_members
        )
        feature_schemas = {tuple(model.feature_names) for model in self.members}
        if len(feature_schemas) != 1:
            raise ValueError("ensemble members do not share one feature schema")
        self.feature_names = next(iter(feature_schemas))
        if any(model.status != "trained" for model in self.members):
            raise ValueError("every ensemble member must be trained")

        raw_duration = dict(self.document.get("duration_priors", {}))
        self.duration_exact = {
            str(key): max(self.duration_floor_minutes, _finite(value, self.duration_floor_minutes))
            for key, value in dict(raw_duration.get("exact", {})).items()
        }
        self.duration_state = {
            str(key): max(self.duration_floor_minutes, _finite(value, self.duration_floor_minutes))
            for key, value in dict(raw_duration.get("state", {})).items()
        }
        self.duration_family = {
            str(key): max(self.duration_floor_minutes, _finite(value, self.duration_floor_minutes))
            for key, value in dict(raw_duration.get("family", {})).items()
        }
        self.duration_global = max(
            self.duration_floor_minutes,
            _finite(raw_duration.get("global", 60.0), 60.0),
        )
        self.training = dict(self.document.get("training", {}))

    @classmethod
    def load(cls, path: str | Path) -> "PeriodRobustEnsemble":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path: str | Path) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def finalize_document(document: Mapping[str, Any]) -> dict[str, Any]:
        output = dict(document)
        output["ensemble_id"] = _stable_id(output)
        return output

    def assert_selectable(self) -> None:
        if self.status != "trained":
            raise RuntimeError(
                f"ensemble {self.ensemble_id!r} has status {self.status!r}"
            )

    @staticmethod
    def _token(value: Any) -> str:
        raw = getattr(value, "value", value)
        return str(raw)

    def expected_duration(self, plan: Any) -> tuple[float, str]:
        family = self._token(getattr(plan, "family"))
        scenario = self._token(getattr(plan, "scenario_path"))
        scale = self._token(getattr(plan, "scale_name"))
        exact_key = f"{family}|{scenario}|{scale}"
        state_key = f"{scenario}|{scale}"
        if exact_key in self.duration_exact:
            return self.duration_exact[exact_key], f"exact:{exact_key}"
        if state_key in self.duration_state:
            return self.duration_state[state_key], f"state:{state_key}"
        if family in self.duration_family:
            return self.duration_family[family], f"family:{family}"
        return self.duration_global, "global"

    def probabilities(self, features: Mapping[str, Any]) -> tuple[float, ...]:
        values = tuple(model.probability(features) for model in self.members)
        if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in values):
            raise RuntimeError("ensemble emitted an invalid probability")
        return values

    def score(
        self,
        features: Mapping[str, Any],
        economics: TradeEconomics,
        plan: Any,
        *,
        risk_fraction: float,
    ) -> RobustScore:
        f = float(risk_fraction)
        if not 0.0 < f < 1.0:
            raise ValueError("risk_fraction must be within (0, 1)")
        if economics.win_net_r <= 0.0 or economics.loss_net_r >= 0.0:
            raise ValueError("trade economics must contain one positive and one negative outcome")

        probabilities = self.probabilities(features)
        center = float(median(probabilities))
        lower = _quantile(probabilities, self.probability_quantile)
        upper = _quantile(probabilities, 1.0 - self.probability_quantile)
        mad = float(median(abs(item - center) for item in probabilities))
        robust_probability = min(1.0, max(0.0, lower))

        target_account_r = economics.win_net_r / abs(economics.loss_net_r)
        expected_account_r = (
            robust_probability * target_account_r
            - (1.0 - robust_probability)
        )
        win_multiplier = 1.0 + f * target_account_r
        loss_multiplier = 1.0 - f
        if win_multiplier <= 0.0 or loss_multiplier <= 0.0:
            raise ValueError("risk geometry creates a nonpositive NAV multiplier")
        expected_log_growth = (
            robust_probability * math.log(win_multiplier)
            + (1.0 - robust_probability) * math.log(loss_multiplier)
        )
        duration, duration_source = self.expected_duration(plan)
        growth_per_hour = expected_log_growth * 60.0 / max(
            self.duration_floor_minutes,
            duration,
        )

        if not math.isfinite(expected_log_growth) or not math.isfinite(growth_per_hour):
            accepted = False
            reason = "NONFINITE_ROBUST_GROWTH"
        elif expected_log_growth <= 0.0:
            accepted = False
            reason = "NONPOSITIVE_ROBUST_LOG_GROWTH"
        elif expected_account_r <= 0.0:
            accepted = False
            reason = "NONPOSITIVE_ROBUST_ACCOUNT_EXPECTANCY"
        else:
            accepted = True
            reason = "POSITIVE_PERIOD_ROBUST_LOG_GROWTH"

        return RobustScore(
            member_probabilities=probabilities,
            probability_median=center,
            probability_lower_quartile=lower,
            probability_upper_quartile=upper,
            probability_mad=mad,
            robust_target_probability=robust_probability,
            target_account_r=target_account_r,
            expected_account_r=expected_account_r,
            expected_log_growth=expected_log_growth,
            expected_duration_minutes=duration,
            expected_log_growth_per_hour=growth_per_hour,
            duration_source=duration_source,
            accepted=accepted,
            reason=reason,
        )


__all__ = [
    "ENSEMBLE_SCHEMA",
    "PeriodRobustEnsemble",
    "RobustScore",
]
