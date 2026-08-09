from __future__ import annotations

import unittest

from cross_asset_repricing_context import CompletedCrossAssetContext
from cross_asset_repricing_logic import PeerAuctionState
from cross_asset_repricing_logic import systemic_repricing_decision


class CrossAssetRepricingLogicTest(unittest.TestCase):
    @staticmethod
    def state(
        symbol: str,
        *,
        ts: int = 60,
        direction: int = -1,
        confirmed: bool = True,
    ) -> PeerAuctionState:
        magnitude = 1.0 if confirmed else 0.01
        return PeerAuctionState(
            symbol=symbol,
            ts_event=ts,
            return_atr=direction * 0.30 * magnitude,
            flow_3m=direction * 0.50 * magnitude,
            efficiency_60s=0.70 if confirmed else 0.20,
            depth_imbalance=direction * 0.20 * magnitude,
        )

    def decision(self, states, *, current_symbol="ETHUSDT", current_ts=120):
        return systemic_repricing_decision(
            trade_side=1,
            current_symbol=current_symbol,
            current_ts=current_ts,
            peer_states=states,
            minimum_return_atr=0.05,
            minimum_efficiency=0.45,
            minimum_directional_depth=0.10,
            maximum_age_ns=65,
        )

    def test_two_of_three_prior_peers_block_opposite_local_reversal(self) -> None:
        result = self.decision(
            [
                self.state("BTCUSDT"),
                self.state("SOLUSDT"),
                self.state("XRPUSDT", confirmed=False),
            ],
        )
        self.assertTrue(result.blocked)
        self.assertEqual(result.confirming_peers, ("BTCUSDT", "SOLUSDT"))

    def test_one_peer_is_not_enough_and_rule_is_mirror_symmetric(self) -> None:
        result = self.decision(
            [
                self.state("BTCUSDT"),
                self.state("SOLUSDT", confirmed=False),
            ],
        )
        self.assertFalse(result.blocked)

        mirrored = systemic_repricing_decision(
            trade_side=-1,
            current_symbol="ETHUSDT",
            current_ts=120,
            peer_states=[
                self.state("BTCUSDT", direction=1),
                self.state("SOLUSDT", direction=1),
            ],
            minimum_return_atr=0.05,
            minimum_efficiency=0.45,
            minimum_directional_depth=0.10,
            maximum_age_ns=65,
        )
        self.assertTrue(mirrored.blocked)
        self.assertEqual(mirrored.repricing_direction, 1)

    def test_same_timestamp_and_stale_peers_never_enter_decision(self) -> None:
        result = self.decision(
            [
                self.state("BTCUSDT", ts=120),
                self.state("SOLUSDT", ts=1),
                self.state("XRPUSDT", ts=60),
            ],
        )
        self.assertFalse(result.blocked)
        self.assertEqual(result.eligible_peers, ("XRPUSDT",))

    def test_registry_result_is_independent_of_same_timestamp_publish_order(self) -> None:
        context = CompletedCrossAssetContext()
        for symbol in ("BTCUSDT", "SOLUSDT", "XRPUSDT"):
            context.publish(self.state(symbol, ts=60))
        # Current-timestamp publications must not overwrite prior-completed
        # evidence for a decision made at ts=120.
        context.publish(self.state("BTCUSDT", ts=120, direction=1))
        peers = context.prior_peer_states(current_symbol="ETHUSDT", current_ts=120)
        result = self.decision(peers)
        self.assertTrue(result.blocked)
        self.assertTrue(all(state.ts_event == 60 for state in peers))

    def test_duplicate_symbol_cannot_count_twice(self) -> None:
        state = self.state("BTCUSDT")
        result = self.decision([state, state, self.state("SOLUSDT", confirmed=False)])
        self.assertFalse(result.blocked)


if __name__ == "__main__":
    unittest.main()
