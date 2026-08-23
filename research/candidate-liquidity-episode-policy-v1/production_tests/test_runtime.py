from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from production.config import ProductionConfig
from production.contracts import ContractError, EpisodePlan, RuntimeMode
from production.event_store import EventStore
from production.risk import size_for_plan


def plan(side: str = "LONG", episode: str = "episode-1") -> EpisodePlan:
    if side == "LONG":
        entry, stop, target = 100.0, 99.0, 102.0
    else:
        entry, stop, target = 100.0, 101.0, 98.0
    return EpisodePlan(
        episode_id=episode,
        action_id=f"action-{episode}",
        symbol="BTCUSDT",
        side=side,
        family="FAILED_AUCTION_REVERSAL",
        order_time_ns=1_700_000_000_000_000_000,
        entry=entry,
        stop=stop,
        target=target,
        gross_rr=2.0,
        planned_target_net_r=1.9,
        entry_geometry="OB_FVG_OVERLAP",
        route_kind="OPPOSING_LIQUIDITY",
    )


class ContractTests(unittest.TestCase):
    def test_geometry_is_enforced(self) -> None:
        self.assertEqual(plan("LONG").risk_fraction_of_price, 0.01)
        self.assertEqual(plan("SHORT").risk_fraction_of_price, 0.01)
        with self.assertRaises(ContractError):
            EpisodePlan(
                episode_id="bad", action_id="bad", symbol="BTCUSDT", side="LONG",
                family="bad", order_time_ns=1, entry=100, stop=101, target=102,
                gross_rr=2, planned_target_net_r=1, entry_geometry="x", route_kind="x",
            )

    def test_testnet_config_is_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            ProductionConfig(mode=RuntimeMode.TESTNET, allow_testnet_orders=False)
        config = ProductionConfig(mode=RuntimeMode.TESTNET, allow_testnet_orders=True)
        self.assertIs(config.mode, RuntimeMode.TESTNET)

    def test_risk_is_capped_by_account_leverage(self) -> None:
        decision = size_for_plan(
            plan(), equity=100_000, risk_fraction=0.03,
            maximum_leverage=3.0, minimum_notional=10.0,
        )
        self.assertLessEqual(decision.effective_leverage, 3.0)
        self.assertGreater(decision.capped_quantity, 0.0)


class EventStoreTests(unittest.TestCase):
    def test_restart_hash_chain_and_global_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "nested" / "runtime.sqlite3"
            store = EventStore(path)
            candidate = plan()
            self.assertTrue(store.enqueue_plan(candidate, ready_for_execution=True))
            self.assertFalse(store.enqueue_plan(candidate, ready_for_execution=True))
            claimed = store.claim_next_plan("consumer-1")
            self.assertIsNotNone(claimed)
            self.assertIsNone(store.claim_next_plan("consumer-2"))
            store.mark_submitted(candidate.decision_id, {"test": True})
            store.complete_decision(candidate.decision_id, "COMPLETED", "TEST")
            before = store.verify_event_chain()
            store.close()
            reopened = EventStore(path)
            after = reopened.verify_event_chain()
            self.assertEqual(before["last_hash"], after["last_hash"])
            self.assertEqual(reopened.account_slot()["state"], "FREE")
            self.assertEqual(reopened.integrity_check(), "ok")
            reopened.close()

    def test_lease_excludes_second_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = EventStore(Path(temp) / "runtime.sqlite3")
            self.assertTrue(store.acquire_lease("producer", "one", 60))
            self.assertFalse(store.acquire_lease("producer", "two", 60))
            store.release_lease("producer", "one")
            self.assertTrue(store.acquire_lease("producer", "two", 60))
            store.close()


if __name__ == "__main__":
    unittest.main()
