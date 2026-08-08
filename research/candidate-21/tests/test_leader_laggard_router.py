from __future__ import annotations

from pathlib import Path
import math
import unittest

from leader_laggard_router import TransferEvidence
from leader_laggard_router import TransferThresholds
from leader_laggard_router import classify_leader_laggard_transfer


class LeaderLaggardRouterTests(unittest.TestCase):
    def test_two_peer_leader_move_and_local_acceleration_confirms(self) -> None:
        decision = classify_leader_laggard_transfer(
            TransferEvidence(
                peer_returns_5m_atr=(1.2, 1.0, -0.1),
                peer_returns_1m_atr=(0.2, 0.1, -0.1),
                own_return_5m_atr=0.2,
                own_return_1m_atr=0.18,
                previous_own_return_1m_atr=0.02,
                close=100.0,
                atr=2.0,
            ),
        )
        self.assertEqual(decision.side, 1)
        self.assertEqual(decision.confirming_peers, 2)
        self.assertGreater(decision.target, 100.0)
        self.assertGreaterEqual(decision.lag_gap_atr, 0.6)

    def test_peer_disagreement_is_unresolved(self) -> None:
        decision = classify_leader_laggard_transfer(
            TransferEvidence(
                peer_returns_5m_atr=(1.2, -1.1, 0.1),
                peer_returns_1m_atr=(0.2, -0.2, 0.0),
                own_return_5m_atr=0.0,
                own_return_1m_atr=0.2,
                previous_own_return_1m_atr=0.0,
                close=100.0,
                atr=2.0,
            ),
        )
        self.assertEqual(decision.side, 0)

    def test_local_reprice_must_accelerate(self) -> None:
        decision = classify_leader_laggard_transfer(
            TransferEvidence(
                peer_returns_5m_atr=(-1.2, -1.0, 0.1),
                peer_returns_1m_atr=(-0.2, -0.1, 0.0),
                own_return_5m_atr=-0.1,
                own_return_1m_atr=-0.1,
                previous_own_return_1m_atr=-0.2,
                close=100.0,
                atr=2.0,
            ),
        )
        self.assertEqual(decision.side, 0)

    def test_short_target_is_below_close(self) -> None:
        decision = classify_leader_laggard_transfer(
            TransferEvidence(
                peer_returns_5m_atr=(-1.3, -1.0, 0.0),
                peer_returns_1m_atr=(-0.2, -0.1, 0.0),
                own_return_5m_atr=-0.2,
                own_return_1m_atr=-0.2,
                previous_own_return_1m_atr=-0.02,
                close=100.0,
                atr=2.0,
            ),
            TransferThresholds(),
        )
        self.assertEqual(decision.side, -1)
        self.assertTrue(math.isfinite(decision.target))
        self.assertLess(decision.target, 100.0)


class LeaderLaggardExecutionContractTests(unittest.TestCase):
    def test_strategy_uses_prior_completed_histories_and_shared_slot_path(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "candidate21_leader_laggard_strategy.py").read_text(
            encoding="utf-8",
        )
        self.assertIn("completed_history(symbol, before_ts=ts", source)
        self.assertIn("_prior_owner", source)
        self.assertIn("self._submit_price_capped_bracket(", source)
        self.assertIn("CROSS_ASSET_PEER_IMPLIED_VALUE", source)
        self.assertNotIn("portfolio simulator", source.lower())


if __name__ == "__main__":
    unittest.main()
