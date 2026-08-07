from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import math
from pathlib import Path
import sys
import types
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

stub = types.ModuleType("v53_nt_core")


@dataclass(frozen=True, slots=True)
class CostConfig:
    entry_fee_rate: Decimal = Decimal("0")
    target_fee_rate: Decimal = Decimal("0")
    stop_fee_rate: Decimal = Decimal("0")
    entry_slippage_rate: Decimal = Decimal("0")
    stop_slippage_rate: Decimal = Decimal("0")
    market_impact_rate: Decimal = Decimal("0")
    funding_rate_allowance: Decimal = Decimal("0")

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "CostConfig":
        return cls(**{name: Decimal(str(values[name])) for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class RotationSignal:
    scenario_id: str
    observed_time_ns: int
    side: str
    entry_reference: float
    stop_price: float
    target_price: float
    cost_after_reward_risk: float
    score: float
    max_hold_minutes: int
    source_feature_open_time_ns: int
    source_feature_available_time_ns: int
    source_max_market_time_ns: int
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }


def cost_after_reward_risk(*, entry: float, stop: float, target: float, side: str, costs: CostConfig) -> float:
    del costs
    risk = entry - stop if side == "BUY" else stop - entry
    reward = target - entry if side == "BUY" else entry - target
    return reward / risk if risk > 0 else math.nan


stub.CostConfig = CostConfig
stub.RotationSignal = RotationSignal
stub.cost_after_reward_risk = cost_after_reward_risk
sys.modules.setdefault("v53_nt_core", stub)
