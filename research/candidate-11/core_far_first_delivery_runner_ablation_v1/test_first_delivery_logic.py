from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import types
import unittest


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class Scenario(Enum):
    FAR = "FAR"
    AAC = "AAC"


@dataclass
class Pool:
    scenario_id: str
    level: float
    source: str = "SESSION"
    range_id: str | None = "R"
    opposite_level: float | None = None
    consumed: bool = False
    external: bool = True
    confirmed_index: int = 0
    expiry_index: int = 999
    strength: int = 1
    confirmed_ts_ns: int = 0


@dataclass
class Auction:
    pool: Pool


@dataclass
class BarObs:
    ts_ns: int
    high: float
    low: float


@dataclass(frozen=True)
class TradePlan:
    scenario_id: str
    scenario: Scenario
    direction: Direction
    expected_entry: float
    stop_price: float
    target_price: float
    loss_per_unit: float
    gain_per_unit: float
    details: dict = field(default_factory=dict)


class CausalAuctionEngine:
    def _costed_limit_plan(self, auction, confirmation_bar, reason):
        return self.plan


logic_module = types.ModuleType("logic")
for name, value in {
    "Auction": Auction,
    "BarObs": BarObs,
    "CausalAuctionEngine": CausalAuctionEngine,
    "Direction": Direction,
    "Scenario": Scenario,
    "TradePlan": TradePlan,
}.items():
    setattr(logic_module, name, value)
sys.modules["logic"] = logic_module

SPEC = importlib.util.spec_from_file_location(
    "first_delivery_logic_under_test",
    Path(__file__).with_name("first_delivery_logic.py"),
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@dataclass
class Config:
    effective_taker_rate: float = 0.0008
    effective_maker_rate: float = 0.0004


@dataclass
class HistoryBar:
    ts_ns: int
    high: float
    low: float


class Engine:
    def __init__(self):
        self.config = Config()
        self._index = 10
        self.pools = []
        self.internal_highs = []
        self.internal_lows = []
        self.bars = []
        self.events = []


class FirstDeliveryLogicTest(unittest.TestCase):
    def plan(self, *, direction=Direction.LONG):
        return TradePlan(
            scenario_id="S",
            scenario=Scenario.FAR,
            direction=direction,
            expected_entry=100.0,
            stop_price=95.0 if direction is Direction.LONG else 105.0,
            target_price=120.0 if direction is Direction.LONG else 80.0,
            loss_per_unit=5.16,
            gain_per_unit=19.912,
            details={"entry_cost_assumption": "TAKER"},
        )

    def test_selects_nearest_live_node_and_self_finances(self):
        engine = Engine()
        auction = Auction(Pool("TRIGGER", 96.0, opposite_level=116.0, confirmed_ts_ns=10))
        # Midpoint 106 is valid.  A later live external pool at 108 is farther.
        engine.pools = [
            auction.pool,
            Pool("EXT", 108.0, source="COMPLETED_4H", confirmed_index=2),
        ]
        plan = self.plan()
        result = MODULE.first_delivery_annotation(
            engine,
            auction,
            BarObs(ts_ns=100, high=102.0, low=99.0),
            plan,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertTrue(result["first_delivery_available"])
        self.assertEqual(result["first_delivery_kind"], "SOURCE_RANGE_EQUILIBRIUM")
        self.assertAlmostEqual(result["first_delivery_target"], 106.0)
        self.assertAlmostEqual(
            result["first_delivery_primary_fraction"]
            * result["first_delivery_net_gain_per_unit"]
            - result["external_runner_fraction"]
            * result["original_costed_loss_per_unit"],
            0.0,
            places=12,
        )
        self.assertAlmostEqual(
            result["first_delivery_primary_fraction"]
            + result["external_runner_fraction"],
            1.0,
            places=12,
        )

    def test_excludes_internal_pivot_consumed_after_becoming_known(self):
        engine = Engine()
        auction = Auction(Pool("TRIGGER", 96.0, opposite_level=130.0, confirmed_ts_ns=10))
        # 104 was known at 50 but crossed at 70, so 106 live pool must win.
        engine.internal_highs = [(40, 50, 104.0)]
        engine.bars = [HistoryBar(70, 105.0, 99.0), HistoryBar(100, 102.0, 99.0)]
        engine.pools = [auction.pool, Pool("EXT", 106.0, confirmed_index=2)]
        result = MODULE.first_delivery_annotation(
            engine,
            auction,
            BarObs(ts_ns=100, high=102.0, low=99.0),
            self.plan(),
        )
        assert result is not None
        self.assertEqual(result["first_delivery_identity"], "EXT")

    def test_excludes_target_already_consumed_by_confirmation_bar(self):
        engine = Engine()
        auction = Auction(Pool("TRIGGER", 96.0, opposite_level=110.0, confirmed_ts_ns=10))
        # Equilibrium 103 is below confirmation high 104 and therefore consumed.
        result = MODULE.first_delivery_annotation(
            engine,
            auction,
            BarObs(ts_ns=100, high=104.0, low=99.0),
            self.plan(),
        )
        assert result is not None
        self.assertFalse(result["first_delivery_available"])
        self.assertEqual(
            result["first_delivery_rejection"],
            "NO_CAUSAL_PRICE_NODE_BEFORE_EXTERNAL_TARGET",
        )

    def test_short_is_symmetric(self):
        engine = Engine()
        auction = Auction(Pool("TRIGGER", 104.0, opposite_level=84.0, confirmed_ts_ns=10))
        result = MODULE.first_delivery_annotation(
            engine,
            auction,
            BarObs(ts_ns=100, high=101.0, low=98.0),
            self.plan(direction=Direction.SHORT),
        )
        assert result is not None
        self.assertTrue(result["first_delivery_available"])
        self.assertAlmostEqual(result["first_delivery_target"], 94.0)

    def test_install_wraps_existing_semantic_plan_once(self):
        engine = CausalAuctionEngine()
        engine.config = Config()
        engine._index = 1
        engine.pools = []
        engine.internal_highs = []
        engine.internal_lows = []
        engine.bars = []
        engine.events = []
        engine.plan = self.plan()
        auction = Auction(Pool("TRIGGER", 96.0, opposite_level=116.0, confirmed_ts_ns=0))
        MODULE.install()
        MODULE.install()
        plan = engine._costed_limit_plan(
            auction,
            BarObs(ts_ns=100, high=102.0, low=99.0),
            "REASON",
        )
        assert plan is not None
        self.assertEqual(plan.details["realization_policy"], MODULE.POLICY)
        self.assertTrue(plan.details["first_delivery_available"])


if __name__ == "__main__":
    unittest.main()
