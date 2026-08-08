from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from logic import Direction as PortfolioDirection
from semantic_execution import MARKET_ENTRY_SENTINEL_NS
from session_auction_bridge import (
    SESSION_LOGIC_KEY,
    SESSION_MODULE,
    adapt_session_plan,
    session_causal_start_ns,
    session_market_semantic,
)
from session_auction_i7 import Direction, EntryOrder, LogicConfig, ScenarioKind, TradePlan


ROOT = Path(__file__).resolve().parent
DAY_NS = 86_400_000_000_000


class SessionI7BridgeTests(unittest.TestCase):
    @staticmethod
    def plan(
        *,
        scenario: ScenarioKind = ScenarioKind.ASIA_HIGH_REJECTION,
        direction: Direction = Direction.SHORT,
        entry_order: EntryOrder = EntryOrder.MARKET,
        expire_ts_ns: int | None = None,
        observed_ts_ns: int = 1_700_000_000_000_000_000,
        details: dict[str, object] | None = None,
    ) -> TradePlan:
        payload: dict[str, object] = {
            "decision_atr": 120.0,
            "source": "ASIA",
            "sweep_ts_ns": observed_ts_ns - 600_000_000_000,
        }
        if details:
            payload.update(details)
        return TradePlan(
            scenario_id=f"BTCUSDT-PERP.BINANCE-{scenario.value}-000001",
            scenario=scenario,
            direction=direction,
            entry_order=entry_order,
            observed_ts_ns=observed_ts_ns,
            expected_entry=30_000.0,
            stop_price=30_300.0 if direction is Direction.SHORT else 29_700.0,
            target_price=29_100.0 if direction is Direction.SHORT else 30_900.0,
            expire_ts_ns=expire_ts_ns,
            loss_per_unit=348.48,
            expected_profit_per_unit=864.36,
            net_r=2.4804,
            details=payload,
        )

    def test_frozen_config_is_loadable_without_mutation(self):
        payload = json.loads((ROOT / "session_i7_config.json").read_text(encoding="utf-8"))
        config = LogicConfig(**payload["logic"])
        self.assertEqual(config.bar_minutes, 5)
        self.assertEqual(config.atr_period, 36)
        self.assertEqual(config.risk_fraction, 0.03)
        self.assertEqual(config.min_net_r, 0.65)
        self.assertEqual(config.price_increment, 0.1)

    def test_failed_auction_maps_to_far_and_uses_the_raid_start(self):
        source = self.plan()
        start = session_causal_start_ns(source)
        self.assertEqual(session_market_semantic(source), "FAR")
        self.assertEqual(start, source.details["sweep_ts_ns"])
        adapted = adapt_session_plan(source)
        self.assertEqual(adapted.details["causal_start_ts_ns"], start)
        self.assertEqual(adapted.details["market_semantic_scenario"], "FAR")

    def test_first_acceptance_maps_to_aac_and_uses_fvg_formation(self):
        observed = 1_700_000_000_000_000_000
        formed = observed - 900_000_000_000
        source = self.plan(
            scenario=ScenarioKind.ASIA_HIGH_ACCEPTANCE,
            direction=Direction.LONG,
            observed_ts_ns=observed,
            details={"fvg_formed_ts_ns": formed},
        )
        self.assertEqual(session_market_semantic(source), "AAC")
        self.assertEqual(session_causal_start_ns(source), formed)

    def test_reacceptance_uses_prior_same_day_failure_not_fresh_fvg_close(self):
        observed = 1_700_000_000_000_000_000
        failure = observed - 1_800_000_000_000
        source = self.plan(
            scenario=ScenarioKind.ASIA_HIGH_REACCEPTANCE,
            direction=Direction.LONG,
            observed_ts_ns=observed,
            details={"fvg_formed_ts_ns": observed, "source": "ASIA"},
        )
        events = [
            SimpleNamespace(
                event_type="HIGH_ACCEPTANCE_FAILED_BACK_INSIDE",
                observed_time_ns=failure,
                details={"source": "ASIA"},
            ),
        ]
        self.assertEqual(session_causal_start_ns(source, events), failure)

    def test_reacceptance_without_same_day_failure_fails_closed(self):
        observed = 1_700_000_000_000_000_000
        source = self.plan(
            scenario=ScenarioKind.ASIA_HIGH_REACCEPTANCE,
            direction=Direction.LONG,
            observed_ts_ns=observed,
            details={"fvg_formed_ts_ns": observed, "source": "ASIA"},
        )
        events = [
            SimpleNamespace(
                event_type="HIGH_ACCEPTANCE_FAILED_BACK_INSIDE",
                observed_time_ns=observed - DAY_NS - 300_000_000_000,
                details={"source": "ASIA"},
            ),
        ]
        self.assertEqual(session_causal_start_ns(source, events), -1)

    def test_market_plan_preserves_costs_and_uses_common_direction(self):
        source = self.plan()
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
        source = self.plan(entry_order=EntryOrder.LIMIT_GTD, expire_ts_ns=expiry)
        adapted = adapt_session_plan(source)
        self.assertEqual(adapted.entry_order_type, "LIMIT")
        self.assertFalse(adapted.entry_post_only)
        self.assertEqual(adapted.expire_ts_ns, expiry)

    def test_adapter_does_not_change_entry_stop_target_order(self):
        source = self.plan()
        adapted = adapt_session_plan(source)
        self.assertGreater(adapted.stop_price, adapted.expected_entry)
        self.assertGreater(adapted.expected_entry, adapted.target_price)
        self.assertEqual(adapted.expected_entry, source.expected_entry)
        self.assertEqual(adapted.stop_price, source.stop_price)
        self.assertEqual(adapted.target_price, source.target_price)


if __name__ == "__main__":
    unittest.main()
