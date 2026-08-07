from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys
import types
from typing import Any, Mapping

from v53_nt_core import CostConfig


@dataclass(frozen=True, slots=True)
class ScheduledSignal:
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


class _Price:
    def __init__(self, value: float) -> None:
        self._value = value

    def as_double(self) -> float:
        return self._value


class _Instrument:
    def make_price(self, value: float) -> _Price:
        return _Price(round(float(value), 1))


class _Cache:
    def __init__(self) -> None:
        self._instrument = _Instrument()

    def instrument(self, instrument_id: str) -> _Instrument:
        assert instrument_id == "BTCUSDT-PERP.BINANCE"
        return self._instrument


class Bar:
    def __init__(self, *, close: float, high: float, low: float, ts_init: int) -> None:
        self.close = _Price(close)
        self.high = _Price(high)
        self.low = _Price(low)
        self.ts_init = ts_init


@dataclass
class _Config:
    instrument_id: str = "BTCUSDT-PERP.BINANCE"
    entry_fee_rate: Decimal = Decimal("0")
    stop_fee_rate: Decimal = Decimal("0")
    entry_slippage_rate: Decimal = Decimal("0")
    stop_slippage_rate: Decimal = Decimal("0")
    market_impact_rate: Decimal = Decimal("0")
    funding_rate_allowance: Decimal = Decimal("0")


class V53RotationStrategy:
    def __init__(self) -> None:
        self.config = _Config()
        self.cache = _Cache()
        self.rejected: list[tuple[ScheduledSignal, int, str]] = []
        self.delegated: list[tuple[ScheduledSignal, Bar]] = []

    def _reject(self, signal: ScheduledSignal, observed_ns: int, reason: str) -> None:
        self.rejected.append((signal, observed_ns, reason))

    def _submit_signal(self, signal: ScheduledSignal, bar: Bar) -> None:
        self.delegated.append((signal, bar))


def _load_adapter_module():
    nautilus = types.ModuleType("nautilus_trader")
    model = types.ModuleType("nautilus_trader.model")
    data = types.ModuleType("nautilus_trader.model.data")
    data.Bar = Bar
    strategy = types.ModuleType("v53_nt_strategy")
    strategy.ScheduledSignal = ScheduledSignal
    strategy.V53RotationStrategy = V53RotationStrategy
    originals = {
        name: sys.modules.get(name)
        for name in (
            "nautilus_trader",
            "nautilus_trader.model",
            "nautilus_trader.model.data",
            "v53_nt_strategy",
        )
    }
    sys.modules["nautilus_trader"] = nautilus
    sys.modules["nautilus_trader.model"] = model
    sys.modules["nautilus_trader.model.data"] = data
    sys.modules["v53_nt_strategy"] = strategy
    try:
        path = Path(__file__).resolve().parents[1] / "v104_nt_strategy.py"
        spec = importlib.util.spec_from_file_location("v104_nt_strategy_isolated", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in originals.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def _signal(
    *,
    activation_ns: int = 2_000,
    stop_price: float = 99.0,
    target_price: float = 110.0,
    target_eligibility_ns: int = 1_000,
) -> ScheduledSignal:
    costs = CostConfig()
    return ScheduledSignal(
        scenario_id="v104-test",
        observed_time_ns=activation_ns,
        side="BUY",
        entry_reference=104.0,
        stop_price=stop_price,
        target_price=target_price,
        cost_after_reward_risk=1.2,
        score=1.0,
        max_hold_minutes=180,
        source_feature_open_time_ns=1_000,
        source_feature_available_time_ns=activation_ns,
        source_max_market_time_ns=activation_ns - 1,
        details={
            "liquidity_boundary": 100.0,
            "minimum_target_cost_after_rr": 1.0,
            "maximum_delivery_fraction": 0.5,
            "old_range_invalidation": 99.5,
            "selected_target_eligibility_ns": target_eligibility_ns,
            "selected_target_expiry_ns": 3_000,
            "activation_validation_costs": {
                name: str(getattr(costs, name))
                for name in costs.__dataclass_fields__
            },
        },
    )


def test_activation_adapter_delegates_only_after_actual_price_revalidation() -> None:
    module = _load_adapter_module()
    strategy = module.V104ExternalLiquidityStrategy()
    bar = Bar(close=104.0, high=104.5, low=103.5, ts_init=2_000)
    strategy._submit_signal(_signal(), bar)
    assert not strategy.rejected
    assert len(strategy.delegated) == 1
    activated, _ = strategy.delegated[0]
    assert activated.entry_reference == 104.0
    assert activated.details["activation_validation_status"] == "ACCEPTED"
    assert activated.details["activation_cost_after_rr"] >= 1.0
    assert activated.stop_price == 99.0
    assert activated.target_price == 110.0


def test_activation_adapter_rejects_bar_which_already_consumed_target() -> None:
    module = _load_adapter_module()
    strategy = module.V104ExternalLiquidityStrategy()
    bar = Bar(close=104.0, high=110.0, low=103.5, ts_init=2_000)
    strategy._submit_signal(_signal(), bar)
    assert not strategy.delegated
    assert len(strategy.rejected) == 1
    rejected, observed_ns, reason = strategy.rejected[0]
    assert observed_ns == 2_000
    assert reason == "ACTIVATION_BAR_PRETRAVERSED_TARGET"
    assert rejected.details["activation_validation_status"] == "REJECTED"


def test_activation_adapter_rejects_target_not_known_by_decision() -> None:
    module = _load_adapter_module()
    strategy = module.V104ExternalLiquidityStrategy()
    bar = Bar(close=104.0, high=104.5, low=103.5, ts_init=2_000)
    strategy._submit_signal(_signal(target_eligibility_ns=2_000), bar)
    assert not strategy.delegated
    assert strategy.rejected[0][2] == "ACTIVATION_TARGET_WAS_NOT_KNOWN_BY_DECISION"


def test_activation_adapter_rejects_structural_invalidation_wick() -> None:
    module = _load_adapter_module()
    strategy = module.V104ExternalLiquidityStrategy()
    bar = Bar(close=104.0, high=104.5, low=99.4, ts_init=2_000)
    strategy._submit_signal(_signal(stop_price=98.0), bar)
    assert not strategy.delegated
    assert strategy.rejected[0][2] == "ACTIVATION_BAR_PRETRAVERSED_STRUCTURAL_INVALIDATION"


def test_activation_adapter_sizes_against_exchange_rounded_stop_and_target() -> None:
    module = _load_adapter_module()
    strategy = module.V104ExternalLiquidityStrategy()
    bar = Bar(close=104.0, high=104.5, low=103.5, ts_init=2_000)
    strategy._submit_signal(_signal(stop_price=98.96, target_price=110.04), bar)
    assert not strategy.rejected
    activated, _ = strategy.delegated[0]
    assert activated.stop_price == 99.0
    assert activated.target_price == 110.0
    assert activated.details["activation_rounded_stop_price"] == 99.0
    assert activated.details["activation_rounded_target_price"] == 110.0
