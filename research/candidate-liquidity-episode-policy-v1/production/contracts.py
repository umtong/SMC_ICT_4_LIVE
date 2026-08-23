from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import math
from typing import Any, Mapping

SYMBOLS: tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")


class RuntimeMode(StrEnum):
    SHADOW = "shadow"
    PAPER = "paper"
    TESTNET = "testnet"


class ContractError(ValueError):
    pass


def _finite(name: str, value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ContractError(f"{name} must be finite")
    return result


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True, slots=True)
class EpisodePlan:
    episode_id: str
    action_id: str
    symbol: str
    side: str
    family: str
    order_time_ns: int
    entry: float
    stop: float
    target: float
    gross_rr: float
    planned_target_net_r: float
    entry_geometry: str
    route_kind: str
    mechanism_coherence: float = 0.0
    expected_log_growth: float | None = None
    probability_edge: float | None = None
    p_fill: float | None = None
    p_target_if_filled: float | None = None
    model_bundle_id: str | None = None
    source_row: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for name in ("episode_id", "action_id", "symbol", "side", "family", "entry_geometry", "route_kind"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ContractError(f"{name} must be non-empty")
        if self.symbol not in SYMBOLS:
            raise ContractError(f"unsupported symbol: {self.symbol}")
        if self.side not in {"LONG", "SHORT"}:
            raise ContractError(f"unsupported side: {self.side}")
        if isinstance(self.order_time_ns, bool) or not isinstance(self.order_time_ns, int) or self.order_time_ns <= 0:
            raise ContractError("order_time_ns must be a positive integer")
        entry = _finite("entry", self.entry)
        stop = _finite("stop", self.stop)
        target = _finite("target", self.target)
        gross_rr = _finite("gross_rr", self.gross_rr)
        target_net_r = _finite("planned_target_net_r", self.planned_target_net_r)
        if min(entry, stop, target) <= 0.0:
            raise ContractError("prices must be positive")
        if self.side == "LONG" and not (stop < entry < target):
            raise ContractError("LONG plan must satisfy stop < entry < target")
        if self.side == "SHORT" and not (target < entry < stop):
            raise ContractError("SHORT plan must satisfy target < entry < stop")
        if gross_rr < 1.0 - 1e-12:
            raise ContractError("gross_rr must be at least 1.0")
        if target_net_r <= 0.0:
            raise ContractError("planned_target_net_r must be positive")
        for optional in (
            "mechanism_coherence",
            "expected_log_growth",
            "probability_edge",
            "p_fill",
            "p_target_if_filled",
        ):
            value = getattr(self, optional)
            if value is not None:
                _finite(optional, value)
        if self.source_row is not None:
            safe = json.loads(json.dumps(dict(self.source_row), ensure_ascii=False, default=str))
            object.__setattr__(self, "source_row", safe)

    @property
    def decision_id(self) -> str:
        payload = {
            "episode_id": self.episode_id,
            "action_id": self.action_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_time_ns": self.order_time_ns,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "model_bundle_id": self.model_bundle_id,
        }
        return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()

    @property
    def risk_fraction_of_price(self) -> float:
        return abs(self.entry - self.stop) / self.entry

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decision_id"] = self.decision_id
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EpisodePlan":
        values = dict(payload)
        values.pop("decision_id", None)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class QuantityDecision:
    symbol: str
    equity: float
    risk_fraction: float
    cash_risk: float
    raw_quantity: float
    capped_quantity: float
    notional: float
    effective_leverage: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
