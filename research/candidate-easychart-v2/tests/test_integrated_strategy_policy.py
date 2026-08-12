from __future__ import annotations

from dataclasses import dataclass
import unittest

from nautilus_trader.model.identifiers import InstrumentId

from domain import Side
from integrated_strategy import EasyChartIntegratedStrategy


@dataclass(frozen=True)
class DummyPlan:
    plan_id: str
    observed_time_ns: int
    family: str
    side: Side = Side.LONG
    entry_order_kind: str = "MARKET"


class EasyChartIntegratedPolicyTest(unittest.TestCase):
    def test_expected_bucket_cardinality_for_four_symbols(self) -> None:
        minute_ns = 60_000_000_000
        self.assertEqual(EasyChartIntegratedStrategy.expected_composite_count(5 * minute_ns, 4), 4)
        self.assertEqual(EasyChartIntegratedStrategy.expected_composite_count(15 * minute_ns, 4), 8)
        self.assertEqual(EasyChartIntegratedStrategy.expected_composite_count(60 * minute_ns, 4), 12)

    def test_confirmed_market_plan_precedes_future_limit_plan_at_same_close(self) -> None:
        instrument = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        market = DummyPlan("market", 100, "TRENDLINE", entry_order_kind="MARKET")
        limit = DummyPlan("limit", 100, "MTF_TOUCH", entry_order_kind="LIMIT")
        ranked = sorted(
            [(instrument, limit), (instrument, market)],
            key=lambda item: EasyChartIntegratedStrategy.arbitration_key(item[0], item[1]),
        )
        self.assertEqual([plan.plan_id for _, plan in ranked], ["market", "limit"])

    def test_remaining_tie_break_is_stable_not_performance_scored(self) -> None:
        btc = InstrumentId.from_str("BTCUSDT-PERP.BINANCE")
        eth = InstrumentId.from_str("ETHUSDT-PERP.BINANCE")
        plans = [
            (eth, DummyPlan("z", 100, "B")),
            (btc, DummyPlan("a", 100, "Z")),
            (btc, DummyPlan("b", 100, "A")),
        ]
        ranked = sorted(plans, key=lambda item: EasyChartIntegratedStrategy.arbitration_key(*item))
        self.assertEqual([plan.plan_id for _, plan in ranked], ["b", "a", "z"])


if __name__ == "__main__":
    unittest.main()
