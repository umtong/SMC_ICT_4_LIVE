from __future__ import annotations

import unittest

from smt_session_context import SmtSessionContext
from smt_session_divergence_logic import PeerSessionState
from smt_session_divergence_logic import local_session_raid_response
from smt_session_divergence_logic import smt_session_divergence


class SmtSessionDivergenceLogicTest(unittest.TestCase):
    @staticmethod
    def peer(
        symbol: str,
        *,
        ts: int = 60,
        high: float = 99.0,
        low: float = 91.0,
    ) -> PeerSessionState:
        return PeerSessionState(
            symbol=symbol,
            ts_event=ts,
            high=high,
            low=low,
            close=(high + low) / 2.0,
            atr=10.0,
            previous_session_high=100.0,
            previous_session_low=90.0,
            flow_15s=0.0,
            flow_60s=0.0,
            depth_imbalance=0.0,
        )

    def decision(self, states, *, kind="HIGH", current_ts=120):
        return smt_session_divergence(
            current_symbol="ETHUSDT",
            current_ts=current_ts,
            swept_kind=kind,
            peer_states=states,
            minimum_penetration_atr=0.08,
            maximum_age_ns=65,
        )

    def test_two_nonconfirming_peers_establish_smt_divergence(self) -> None:
        result = self.decision(
            [
                self.peer("BTCUSDT", high=100.5),
                self.peer("SOLUSDT", high=99.5),
                self.peer("XRPUSDT", high=101.0),
            ],
        )
        # With ATR 10 and penetration 0.08, 100.8 is the sweep threshold.
        self.assertTrue(result.confirmed)
        self.assertEqual(result.same_side_sweep_peers, ("XRPUSDT",))
        self.assertEqual(result.nonconfirming_peers, ("BTCUSDT", "SOLUSDT"))

    def test_two_peer_sweeps_confirm_market_direction_and_cancel_divergence(self) -> None:
        result = self.decision(
            [
                self.peer("BTCUSDT", high=101.0),
                self.peer("SOLUSDT", high=101.2),
                self.peer("XRPUSDT", high=99.5),
            ],
        )
        self.assertFalse(result.confirmed)
        self.assertEqual(len(result.same_side_sweep_peers), 2)

    def test_same_timestamp_and_stale_peer_states_are_excluded(self) -> None:
        result = self.decision(
            [
                self.peer("BTCUSDT", ts=120),
                self.peer("SOLUSDT", ts=1),
                self.peer("XRPUSDT", ts=60),
            ],
        )
        self.assertFalse(result.confirmed)
        self.assertEqual(result.valid_peers, ("XRPUSDT",))

    def test_local_response_is_mirror_symmetric(self) -> None:
        self.assertTrue(
            local_session_raid_response(
                side=-1,
                swept_kind="HIGH",
                boundary=100.0,
                close=99.0,
                flow_15s=-0.3,
                flow_60s=0.0,
                depth_imbalance=-0.2,
                minimum_tail_improvement=0.1,
                minimum_directional_depth=0.1,
            ),
        )
        self.assertTrue(
            local_session_raid_response(
                side=1,
                swept_kind="LOW",
                boundary=100.0,
                close=101.0,
                flow_15s=0.3,
                flow_60s=0.0,
                depth_imbalance=0.2,
                minimum_tail_improvement=0.1,
                minimum_directional_depth=0.1,
            ),
        )

    def test_context_uses_latest_strictly_prior_state_not_publish_order(self) -> None:
        context = SmtSessionContext()
        context.publish(self.peer("BTCUSDT", ts=60))
        context.publish(self.peer("BTCUSDT", ts=120, high=105.0))
        context.publish(self.peer("SOLUSDT", ts=60))
        peers = context.prior_peer_states(current_symbol="ETHUSDT", current_ts=120)
        self.assertEqual({state.ts_event for state in peers}, {60})


if __name__ == "__main__":
    unittest.main()
