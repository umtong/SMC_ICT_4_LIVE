from __future__ import annotations

from dataclasses import dataclass
import unittest

from c10_v28_overlay import ResolvedLeadershipGateAdapter


@dataclass(frozen=True)
class Decision:
    approved: bool
    reason: str
    leader: str
    symbol: str
    scenario: str
    confirmation_impulse: float | None
    directional_trend_scores: dict[str, float]


class StubGate:
    severe_adverse_trend_score = -1.5
    minimum_follower_confirmation_impulse = 1.0

    def __init__(self, decision: Decision) -> None:
        self.decision = decision

    def observe_batch(self, *args, **kwargs):
        return None

    def decide(self, *args, **kwargs):
        return self.decision


class ResolvedAuctionCertificateTest(unittest.TestCase):
    def test_rejects_market_wide_unresolved_adverse_auction(self):
        base = Decision(
            approved=True,
            reason="LEADER_DIRECTIONAL_ALIGNMENT",
            leader="BTCUSDT",
            symbol="BTCUSDT",
            scenario="FAR",
            confirmation_impulse=2.0,
            directional_trend_scores={
                "BTCUSDT": -1.9,
                "ETHUSDT": -1.8,
                "SOLUSDT": -2.1,
                "XRPUSDT": -1.7,
            },
        )
        result = ResolvedLeadershipGateAdapter(StubGate(base), ablated=False).decide()
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "UNRESOLVED_MARKET_WIDE_ADVERSE_AUCTION")

    def test_follower_relative_recovery_requires_existing_impulse_minimum(self):
        base = Decision(
            approved=True,
            reason="FOLLOWER_FAR_DIRECTIONAL_LEADER_RECOVERY",
            leader="BTCUSDT",
            symbol="ETHUSDT",
            scenario="FAR",
            confirmation_impulse=0.9,
            directional_trend_scores={
                "BTCUSDT": 0.3,
                "ETHUSDT": 0.2,
                "SOLUSDT": 0.1,
                "XRPUSDT": 0.0,
            },
        )
        result = ResolvedLeadershipGateAdapter(StubGate(base), ablated=False).decide()
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "FOLLOWER_FAR_WEAK_LOCAL_DISPLACEMENT")

    def test_ablation_preserves_original_decision(self):
        base = Decision(
            approved=True,
            reason="LEADER_DIRECTIONAL_ALIGNMENT",
            leader="BTCUSDT",
            symbol="BTCUSDT",
            scenario="FAR",
            confirmation_impulse=0.5,
            directional_trend_scores={
                "BTCUSDT": -2.0,
                "ETHUSDT": -2.0,
                "SOLUSDT": -2.0,
                "XRPUSDT": -2.0,
            },
        )
        result = ResolvedLeadershipGateAdapter(StubGate(base), ablated=True).decide()
        self.assertEqual(result, base)


if __name__ == "__main__":
    unittest.main()
