"""Small causal response policy shared by research and Nautilus execution.

The upstream auction engines produce complete, immutable structural plans.  This
module does only three things:

1. preserves plans whose entry follows a completed liquidity/structure response;
2. estimates whether one structural-risk unit of favorable response will trade
   before the plan's causal invalidation;
3. replaces the distant structural destination with a single pre-entry first
   response objective at 1R, without changing the stop or position risk.

There are no symbol/date features, no partial exits, no daily limits and no
fallback strategy.  The same feature builder is used by offline training and
live/backtest inference.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd
from catboost import CatBoostClassifier

from contracts_v5 import V5TradePlan
from domain import Side
from robust_router_system import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
)

MODEL_VERSION = "easychart-c-causal-first-response-v2"
FIRST_OBJECTIVE_R = 1.0
MAX_TARGET_COST_R = 0.25
DEFAULT_SCORE_QUANTILE = 0.575
EXCLUDED_TRIGGER_KINDS: tuple[str, ...] = (
    "FLOW_BUY_INITIATIVE",
    "EFFICIENT_PULLBACK_FIRST_RESPONSE_ADVERSE_FLOW_ABSORBED",
)
# These are not asset/date exceptions.  They are two structurally incomplete
# state/action pairings which the development environments exposed repeatedly:
# fading the upper edge of an *ascending* channel before control has transferred,
# and buying an H4 low sweep from the coarse reclaim alone.  Both are held out
# until a lower-timeframe transfer/acceptance mechanism exists in the shared
# opportunity universe.
EXCLUDED_HIGHER_ZONE_KINDS: tuple[str, ...] = (
    "ASCENDING_CHANNEL_UPPER",
    "PREVIOUS_H4_LOW",
)
FEATURES: tuple[str, ...] = tuple(NUMERIC_FEATURES) + tuple(CATEGORICAL_FEATURES)


def _plain(value: Any) -> Any:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if name is not None and not isinstance(value, str):
        return name
    enum_value = getattr(value, "value", None)
    if enum_value is not None and not isinstance(value, str):
        return enum_value
    return value


def feature_frame(records: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Return CatBoost input with a fixed train/live schema."""

    frame = pd.DataFrame(records).reindex(columns=FEATURES)
    for name in CATEGORICAL_FEATURES:
        frame[name] = frame[name].map(_plain).fillna("__MISSING__").astype(str)
    for name in NUMERIC_FEATURES:
        frame[name] = pd.to_numeric(frame[name], errors="coerce").replace(
            [np.inf, -np.inf],
            np.nan,
        )
    return frame


def target_cost_r(gross_rr: float, target_net_r: float) -> float:
    return float(gross_rr) - float(target_net_r)


def _rounded_first_objective(
    *,
    side: Side,
    entry: float,
    stop: float,
    tick_size: float,
    objective_r: float,
) -> float:
    risk = abs(float(entry) - float(stop))
    if risk <= 0.0:
        raise ValueError("entry and stop must define positive risk")
    if objective_r < 1.0:
        raise ValueError("first objective must be at least 1R")
    raw = float(entry) + (risk * objective_r if side is Side.LONG else -risk * objective_r)
    tick = Decimal(str(tick_size))
    units = Decimal(str(raw)) / tick
    rounded_units = units.to_integral_value(
        rounding=ROUND_CEILING if side is Side.LONG else ROUND_FLOOR,
    )
    return float(rounded_units * tick)


def replace_with_first_objective(
    plan: V5TradePlan,
    *,
    tick_size: float,
    objective_r: float = FIRST_OBJECTIVE_R,
) -> V5TradePlan:
    """Freeze a one-shot 1R response objective before order submission.

    The original structural target remains useful as context for the model.  The
    executable target becomes the first completed response objective.  The stop,
    causal event identity and all entry geometry are unchanged.
    """

    target = _rounded_first_objective(
        side=plan.side,
        entry=float(plan.entry),
        stop=float(plan.stop),
        tick_size=float(tick_size),
        objective_r=float(objective_r),
    )
    risk = abs(float(plan.entry) - float(plan.stop))
    gross_rr = abs(target - float(plan.entry)) / risk
    provenance = tuple(plan.rule_provenance) + (
        "RESEARCH_SYNTHESIS:CONFIRMED_LIQUIDITY_RESPONSE_COMPLETES_AT_ONE_STRUCTURAL_RISK_UNIT_BEFORE_THE_DISTANT_DESTINATION",
    )
    return replace(
        plan,
        target=target,
        gross_rr=gross_rr,
        target_zone_id=f"{plan.target_zone_id}|FIRST_RESPONSE_{objective_r:.3f}R",
        target_zone_kind="CAUSAL_FIRST_RESPONSE_OBJECTIVE",
        rule_provenance=provenance,
        source_rule_count=len(provenance),
    )


@dataclass(frozen=True, slots=True)
class Decision:
    accepted: bool
    probability_target_first: float
    threshold: float
    target_cost_r: float
    target_net_r: float
    stop_net_r: float
    expected_net_r: float
    expected_log_growth: float
    reason: str


class CausalResponseRouter:
    """CatBoost probability router with explicit causal/geometry eligibility."""

    def __init__(
        self,
        model: CatBoostClassifier,
        metadata: Mapping[str, Any],
    ) -> None:
        self.model = model
        self.metadata = dict(metadata)
        self.threshold = float(self.metadata["probability_threshold"])
        self.max_target_cost_r = float(
            self.metadata.get("max_target_cost_r", MAX_TARGET_COST_R),
        )
        self.objective_r = float(self.metadata.get("first_objective_r", FIRST_OBJECTIVE_R))
        self.risk_fraction = float(self.metadata.get("risk_fraction", 0.03))
        self.trained_through_ns = int(self.metadata["trained_through_ns"])
        self.excluded_trigger_kinds = frozenset(
            self.metadata.get("excluded_trigger_kinds", EXCLUDED_TRIGGER_KINDS),
        )
        self.excluded_higher_zone_kinds = frozenset(
            self.metadata.get(
                "excluded_higher_zone_kinds",
                EXCLUDED_HIGHER_ZONE_KINDS,
            ),
        )
        if tuple(self.metadata.get("features", ())) != FEATURES:
            raise RuntimeError("model feature schema does not match runtime")
        if not 0.0 < self.threshold < 1.0:
            raise ValueError("probability threshold must lie in (0, 1)")
        if self.objective_r < 1.0:
            raise ValueError("objective R must be >= 1")

    @classmethod
    def load(cls, model_path: Path, metadata_path: Path | None = None) -> "CausalResponseRouter":
        model_path = Path(model_path)
        if metadata_path is None:
            metadata_path = model_path.with_suffix(".json")
        metadata = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        model = CatBoostClassifier()
        model.load_model(str(model_path))
        return cls(model, metadata)

    def probability(self, record: Mapping[str, Any]) -> float:
        value = float(self.model.predict_proba(feature_frame([record]))[0, 1])
        if not math.isfinite(value):
            raise RuntimeError("non-finite model probability")
        return value

    def decision(
        self,
        record: Mapping[str, Any],
        *,
        fixed_target_economics: Mapping[str, float],
    ) -> Decision:
        trigger_kind = str(_plain(record.get("trigger_zone_kind")) or "__MISSING__")
        higher_zone_kind = str(
            _plain(record.get("higher_zone_kind")) or "__MISSING__"
        )
        original_rr = float(record.get("gross_rr", float("nan")))
        original_target_net_r = float(record.get("target_net_r", float("nan")))
        cost = target_cost_r(original_rr, original_target_net_r)
        target_net = float(fixed_target_economics["target_net_r"])
        stop_net = float(fixed_target_economics["stop_net_r"])

        if trigger_kind in self.excluded_trigger_kinds:
            return Decision(False, float("nan"), self.threshold, cost, target_net, stop_net, float("nan"), float("nan"), "EXCLUDED_RESPONSE_KIND")
        if higher_zone_kind in self.excluded_higher_zone_kinds:
            return Decision(False, float("nan"), self.threshold, cost, target_net, stop_net, float("nan"), float("nan"), "INCOMPLETE_HIGHER_TIMEFRAME_CONTROL_TRANSFER")
        if not math.isfinite(original_rr) or original_rr < 1.0:
            return Decision(False, float("nan"), self.threshold, cost, target_net, stop_net, float("nan"), float("nan"), "ORIGINAL_PLAN_RR_BELOW_ONE")
        if not math.isfinite(cost) or cost > self.max_target_cost_r:
            return Decision(False, float("nan"), self.threshold, cost, target_net, stop_net, float("nan"), float("nan"), "EXECUTION_COST_CONSUMES_TOO_MUCH_STRUCTURE_RISK")
        if target_net <= 0.0 or stop_net >= 0.0:
            return Decision(False, float("nan"), self.threshold, cost, target_net, stop_net, float("nan"), float("nan"), "NONVIABLE_FIXED_OBJECTIVE_ECONOMICS")

        probability = self.probability(record)
        expected_net = probability * target_net + (1.0 - probability) * stop_net
        win_factor = 1.0 + self.risk_fraction * target_net
        loss_factor = 1.0 + self.risk_fraction * stop_net
        expected_log = (
            probability * math.log(win_factor)
            + (1.0 - probability) * math.log(loss_factor)
            if win_factor > 0.0 and loss_factor > 0.0
            else float("-inf")
        )
        accepted = probability >= self.threshold and expected_log > 0.0
        return Decision(
            accepted,
            probability,
            self.threshold,
            cost,
            target_net,
            stop_net,
            expected_net,
            expected_log,
            "ACCEPTED" if accepted else "PROBABILITY_OR_POST_COST_EXPECTANCY_INSUFFICIENT",
        )
