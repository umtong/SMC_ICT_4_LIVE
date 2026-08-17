"""Portable ML selector and post-cost trade economics for EasyChart ML1.

The live/backtest strategy must not depend on scikit-learn.  Training exports a
small JSON forest and this module evaluates it using only the Python standard
library.  The deterministic EasyChart engines still own the causal setup,
entry, stop and target.  ML1 estimates only the conditional probability that
the frozen target is reached before the frozen stop and may abstain.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


MODEL_SCHEMA = "easychart_ml1_portable_binary_v1"
_EPS = 1e-9


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clip(value: float, lower: float, upper: float) -> float:
    return min(upper, max(lower, value))


def _sigmoid(value: float) -> float:
    if value >= 0.0:
        z = math.exp(-min(value, 60.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(value, -60.0))
    return z / (1.0 + z)


def _logit(probability: float) -> float:
    p = _clip(probability, _EPS, 1.0 - _EPS)
    return math.log(p / (1.0 - p))


@dataclass(frozen=True, slots=True)
class TradeEconomics:
    """Target/stop outcomes in R after conservative fees and slippage."""

    planned_risk: float
    planned_reward: float
    gross_rr: float
    win_net_r: float
    loss_net_r: float
    break_even_probability: float
    entry_fill: float
    target_fill: float
    stop_fill: float
    estimated_win_cost_r: float
    estimated_loss_cost_r: float

    def expected_net_r(self, target_probability: float) -> float:
        p = _clip(float(target_probability), 0.0, 1.0)
        return p * self.win_net_r + (1.0 - p) * self.loss_net_r


@dataclass(frozen=True, slots=True)
class ModelDecision:
    raw_probability: float
    target_probability: float
    tree_probability_std: float
    expected_net_r: float
    required_probability: float
    accepted: bool
    reason: str


def estimate_trade_economics(
    *,
    side: Any,
    entry: float,
    stop: float,
    target: float,
    tick_size: float,
    entry_fee_rate: float,
    target_fee_rate: float,
    stop_fee_rate: float,
    funding_rate: float = 0.0,
    entry_slippage_ticks: int = 0,
    target_slippage_ticks: int = 0,
    stop_slippage_ticks: int = 0,
) -> TradeEconomics:
    """Estimate both frozen exits in planned-risk units.

    Slippage is adverse on every leg.  Funding is charged conservatively once on
    entry notional for both outcomes; the model is therefore not rewarded for a
    trade whose apparent edge is only smaller execution assumptions.
    """

    entry = _finite(entry)
    stop = _finite(stop)
    target = _finite(target)
    tick = abs(_finite(tick_size))
    if tick <= 0.0:
        raise ValueError("tick_size must be positive")
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if entry <= 0.0 or risk <= 0.0 or reward <= 0.0:
        raise ValueError("entry, stop and target must define positive geometry")

    side_text = getattr(side, "name", side)
    side_text = str(side_text).upper()
    if side_text.endswith("LONG") or side_text == "BUY":
        sign = 1.0
        if not (stop < entry < target):
            raise ValueError("long geometry must satisfy stop < entry < target")
    elif side_text.endswith("SHORT") or side_text == "SELL":
        sign = -1.0
        if not (target < entry < stop):
            raise ValueError("short geometry must satisfy target < entry < stop")
    else:
        raise ValueError(f"unknown side {side!r}")

    if min(entry_slippage_ticks, target_slippage_ticks, stop_slippage_ticks) < 0:
        raise ValueError("slippage ticks cannot be negative")
    rates = (entry_fee_rate, target_fee_rate, stop_fee_rate, funding_rate)
    if any(_finite(item) < 0.0 for item in rates):
        raise ValueError("fee and funding rates cannot be negative")

    # Market entry: buy fills higher, sell fills lower.  Exit slippage has the
    # opposite price sign because it is an order in the opposite direction.
    entry_fill = entry + sign * int(entry_slippage_ticks) * tick
    target_fill = target - sign * int(target_slippage_ticks) * tick
    stop_fill = stop - sign * int(stop_slippage_ticks) * tick

    win_gross_r = sign * (target_fill - entry_fill) / risk
    loss_gross_r = sign * (stop_fill - entry_fill) / risk
    entry_fee_r = abs(entry_fill) * _finite(entry_fee_rate) / risk
    target_fee_r = abs(target_fill) * _finite(target_fee_rate) / risk
    stop_fee_r = abs(stop_fill) * _finite(stop_fee_rate) / risk
    funding_r = abs(entry_fill) * _finite(funding_rate) / risk
    win_net_r = win_gross_r - entry_fee_r - target_fee_r - funding_r
    loss_net_r = loss_gross_r - entry_fee_r - stop_fee_r - funding_r
    denominator = win_net_r - loss_net_r
    break_even = -loss_net_r / denominator if denominator > 0.0 else 1.0

    return TradeEconomics(
        planned_risk=risk,
        planned_reward=reward,
        gross_rr=reward / risk,
        win_net_r=win_net_r,
        loss_net_r=loss_net_r,
        break_even_probability=_clip(break_even, 0.0, 1.0),
        entry_fill=entry_fill,
        target_fill=target_fill,
        stop_fill=stop_fill,
        estimated_win_cost_r=(reward / risk) - win_net_r,
        estimated_loss_cost_r=abs(loss_net_r) - 1.0,
    )


class PortableBinaryModel:
    """Evaluate a constant or exported binary tree ensemble from JSON."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = dict(document)
        if self.document.get("schema") != MODEL_SCHEMA:
            raise ValueError(
                f"unsupported model schema {self.document.get('schema')!r}; expected {MODEL_SCHEMA!r}",
            )
        self.model_type = str(self.document.get("model_type", ""))
        self.status = str(self.document.get("status", "unknown"))
        self.model_id = str(self.document.get("model_id", "unidentified"))
        self.feature_names = tuple(str(item) for item in self.document.get("feature_names", ()))
        if len(self.feature_names) != len(set(self.feature_names)):
            raise ValueError("model feature_names must be unique")
        self.defaults = {
            str(key): _finite(value)
            for key, value in dict(self.document.get("feature_defaults", {})).items()
        }
        self.clip_ranges = {
            str(key): (_finite(value[0], -1e6), _finite(value[1], 1e6))
            for key, value in dict(self.document.get("feature_clip_ranges", {})).items()
            if isinstance(value, Sequence) and len(value) == 2
        }
        self.calibration = dict(self.document.get("calibration", {"kind": "identity"}))
        self.decision_policy = dict(self.document.get("decision", {}))
        self._validate_structure()

    @classmethod
    def load(cls, path: str | Path) -> "PortableBinaryModel":
        model_path = Path(path)
        document = json.loads(model_path.read_text(encoding="utf-8"))
        return cls(document)

    @staticmethod
    def stable_id(document: Mapping[str, Any]) -> str:
        payload = dict(document)
        payload.pop("model_id", None)
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _validate_structure(self) -> None:
        if self.model_type == "constant_probability":
            probability = _finite(self.document.get("constant_probability"), -1.0)
            if not 0.0 <= probability <= 1.0:
                raise ValueError("constant_probability must be within [0, 1]")
            return
        if self.model_type != "extra_trees_binary":
            raise ValueError(f"unsupported model_type {self.model_type!r}")
        trees = self.document.get("trees")
        if not isinstance(trees, list) or not trees:
            raise ValueError("tree ensemble must contain at least one tree")
        for tree_index, tree in enumerate(trees):
            nodes = tree.get("nodes") if isinstance(tree, Mapping) else None
            if not isinstance(nodes, list) or not nodes:
                raise ValueError(f"tree {tree_index} has no nodes")
            for node_index, node in enumerate(nodes):
                if not isinstance(node, Mapping):
                    raise ValueError(f"tree {tree_index} node {node_index} is not a mapping")
                leaf_probability = node.get("probability")
                if leaf_probability is not None:
                    p = _finite(leaf_probability, -1.0)
                    if not 0.0 <= p <= 1.0:
                        raise ValueError(f"invalid leaf probability in tree {tree_index}")
                    continue
                feature = int(node.get("feature", -1))
                left = int(node.get("left", -1))
                right = int(node.get("right", -1))
                if feature < 0 or feature >= len(self.feature_names):
                    raise ValueError(f"invalid feature index in tree {tree_index} node {node_index}")
                if left < 0 or right < 0 or left >= len(nodes) or right >= len(nodes):
                    raise ValueError(f"invalid child index in tree {tree_index} node {node_index}")

    def assert_selectable(self, *, allow_shadow_model: bool = False) -> None:
        if self.status == "trained":
            return
        if allow_shadow_model:
            return
        raise RuntimeError(
            f"model {self.model_id!r} has status {self.status!r}; select mode requires a trained model",
        )

    def vector(self, features: Mapping[str, Any]) -> tuple[float, ...]:
        values: list[float] = []
        for name in self.feature_names:
            value = _finite(features.get(name, self.defaults.get(name, 0.0)), self.defaults.get(name, 0.0))
            lower, upper = self.clip_ranges.get(name, (-1e6, 1e6))
            values.append(_clip(value, lower, upper))
        return tuple(values)

    @staticmethod
    def _tree_probability(tree: Mapping[str, Any], vector: Sequence[float]) -> float:
        nodes = tree["nodes"]
        index = 0
        # A malformed cycle should never be exported, but the bound converts it
        # into a loud failure rather than an infinite live loop.
        for _ in range(len(nodes) + 1):
            node = nodes[index]
            probability = node.get("probability")
            if probability is not None:
                return _clip(_finite(probability, 0.5), 0.0, 1.0)
            feature = int(node["feature"])
            threshold = _finite(node["threshold"])
            index = int(node["left"] if vector[feature] <= threshold else node["right"])
        raise RuntimeError("tree traversal exceeded node count; model may contain a cycle")

    def raw_tree_probabilities(self, features: Mapping[str, Any]) -> tuple[float, ...]:
        if self.model_type == "constant_probability":
            return (_finite(self.document["constant_probability"], 0.5),)
        vector = self.vector(features)
        return tuple(self._tree_probability(tree, vector) for tree in self.document["trees"])

    def raw_probability(self, features: Mapping[str, Any]) -> float:
        probabilities = self.raw_tree_probabilities(features)
        return sum(probabilities) / len(probabilities)

    def calibrate(self, raw_probability: float) -> float:
        kind = str(self.calibration.get("kind", "identity"))
        raw = _clip(_finite(raw_probability, 0.5), _EPS, 1.0 - _EPS)
        if kind == "identity":
            return raw
        if kind == "platt_logit":
            coefficient = _finite(self.calibration.get("coefficient"), 1.0)
            intercept = _finite(self.calibration.get("intercept"), 0.0)
            return _clip(_sigmoid(coefficient * _logit(raw) + intercept), _EPS, 1.0 - _EPS)
        raise ValueError(f"unsupported calibration kind {kind!r}")

    def probability(self, features: Mapping[str, Any]) -> float:
        return self.calibrate(self.raw_probability(features))

    def decide(
        self,
        features: Mapping[str, Any],
        economics: TradeEconomics,
        *,
        min_probability: float | None = None,
        min_expected_net_r: float | None = None,
        probability_edge: float | None = None,
    ) -> ModelDecision:
        tree_probabilities = self.raw_tree_probabilities(features)
        raw = sum(tree_probabilities) / len(tree_probabilities)
        probability = self.calibrate(raw)
        mean = raw
        variance = sum((item - mean) ** 2 for item in tree_probabilities) / len(tree_probabilities)
        tree_std = math.sqrt(max(0.0, variance))

        policy_min_probability = _finite(self.decision_policy.get("min_probability"), 0.0)
        policy_min_ev = _finite(self.decision_policy.get("min_expected_net_r"), 0.0)
        policy_edge = _finite(self.decision_policy.get("probability_edge"), 0.0)
        p_floor = policy_min_probability if min_probability is None else float(min_probability)
        ev_floor = policy_min_ev if min_expected_net_r is None else float(min_expected_net_r)
        edge = policy_edge if probability_edge is None else float(probability_edge)
        required_probability = max(
            _clip(p_floor, 0.0, 1.0),
            _clip(economics.break_even_probability + max(0.0, edge), 0.0, 1.0),
        )
        expected = economics.expected_net_r(probability)

        if economics.win_net_r <= 0.0:
            accepted = False
            reason = "NONPOSITIVE_POST_COST_WIN"
        elif economics.loss_net_r >= 0.0:
            accepted = False
            reason = "INVALID_POST_COST_LOSS"
        elif probability < required_probability:
            accepted = False
            reason = "PROBABILITY_BELOW_REQUIRED"
        elif expected < ev_floor:
            accepted = False
            reason = "EXPECTED_NET_R_BELOW_REQUIRED"
        else:
            accepted = True
            reason = "MODEL_EXPECTANCY_ACCEPTED"

        return ModelDecision(
            raw_probability=raw,
            target_probability=probability,
            tree_probability_std=tree_std,
            expected_net_r=expected,
            required_probability=required_probability,
            accepted=accepted,
            reason=reason,
        )


def make_shadow_document(
    feature_names: Sequence[str],
    *,
    probability: float = 0.5,
) -> dict[str, Any]:
    """Build a non-selectable wiring model for deterministic shadow runs."""

    document: dict[str, Any] = {
        "schema": MODEL_SCHEMA,
        "model_type": "constant_probability",
        "status": "shadow_only",
        "model_id": "pending",
        "feature_names": list(feature_names),
        "feature_defaults": {name: 0.0 for name in feature_names},
        "feature_clip_ranges": {},
        "constant_probability": _clip(float(probability), 0.0, 1.0),
        "calibration": {"kind": "identity"},
        "decision": {
            "min_probability": 0.0,
            "probability_edge": 0.0,
            "min_expected_net_r": 0.0,
        },
        "training": {
            "note": "Wiring-only model. Harvest causal plan features and train before select mode.",
        },
    }
    document["model_id"] = PortableBinaryModel.stable_id(document)
    return document
