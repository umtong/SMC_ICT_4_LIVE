from __future__ import annotations

import json
from pathlib import Path
import unittest

from logic import Direction as PortfolioDirection
from semantic_execution import MARKET_ENTRY_SENTINEL_NS
from session_auction_bridge import (
    SESSION_LOGIC_KEY,
    SESSION_MODULE,
    adapt_session_plan,
)
from session_auction_i7 import Direction, EntryOrder, LogicConfig, ScenarioKind, TradePlan


ROOT = Path(__file__).resolve().parent


class SessionI7BridgeTests(unittest.TestCase):
    @staticmethod
    def plan(entry_order: EntryOrder, expire_ts_ns: int | None) -> TradePlan:
        return TradePlan(
            scenario_id="BTCUSDT-PERP.BINANCE-ASIA-HIGH-RAID-000001",
            scenario=ScenarioKind.ASIA_HIGH_REJECTION,
            direction=Direction.SHORT,
            entry_order=entry_order,
            observed_ts_ns=1_700_000_000_000_000_000,
            expected_entry=30_000.0,
            stop_price=30_300.0,
            target_price=29_100.0,
            expire_ts_ns=expire_ts_ns,
            loss_per_unit=348.48,
            expected_profit_per_unit=864.36,
            net_r=2.4804,
            details={"decision_atr": 120.0, "source": "ASIA"},
        )

    def test_frozen_config_is_loadable_without_mutation(self):
        payload = json.loads((ROOT / "session_i7_config.json").read_text(encoding="utf-8"))
        config = LogicConfig(**payload["logic"])
        self.assertEqual(config.bar_minutes, 5)
        self.assertEqual(config.atr_period, 36)
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(config.min_net_r, 0.65)
        self.assertEqual(config.price_increment, 0.1)

    def test_market_plan_preserves_costs_and_uses_common_direction(self):
        source = self.plan(EntryOrder.MARKET, None)
        adapted = adapt_session_plan(source)
        self.assertEqual(adapted.direction, PortfolioDirection.SHORT)
        self.assertEqual(adapted.entry_order_type, "MARKET")
        self.assertFalse(adapted.entry_post_only)
        self.assertEqual(adapted.expire_ts_ns, MARKET_ENTRY_SENTINEL_NS)
        self.assertEqual(adapted.loss_per_unit, source.loss_per_unit)
        self.assertEqual(adapted.gain_per_unit, source.expected_profit_per_unit)
        self.assertEqual(adapted.net_r, source.net_r)
        self.assertEqual(adapted.details["_logic_key"], SESSION_LOGIC_KEY)
        self.assertEqual(adapted.details["module"], SESSION_MODULE)

    def test_protected_limit_remains_marketable_and_preserves_expiry(self):
        expiry = 1_700_000_300_000_000_000
        source = self.plan(EntryOrder.LIMIT_GTD, expiry)
        adapted = adapt_session_plan(source)
        self.assertEqual(adapted.entry_order_type, "LIMIT")
        self.assertFalse(adapted.entry_post_only)
        self.assertEqual(adapted.expire_ts_ns, expiry)
        self.assertEqual(adapted.scenario.value, "ASIA_HIGH_REJECTION")

    def test_adapter_does_not_change_entry_stop_target_order(self):
        source = self.plan(EntryOrder.MARKET, None)
        adapted = adapt_session_plan(source)
        self.assertGreater(adapted.stop_price, adapted.expected_entry)
        self.assertGreater(adapted.expected_entry, adapted.target_price)
        self.assertEqual(adapted.expected_entry, source.expected_entry)
        self.assertEqual(adapted.stop_price, source.stop_price)
        self.assertEqual(adapted.target_price, source.target_price)


if __name__ == "__main__":
    unittest.main()
