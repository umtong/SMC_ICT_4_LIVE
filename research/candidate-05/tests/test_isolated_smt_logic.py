from __future__ import annotations

import unittest

from isolated_smt_context import IsolatedSmtContext
from isolated_smt_logic import PeerMicroState
from isolated_smt_logic import isolated_smt_reversal_context
from smt_session_divergence_logic import SmtSessionDecision


class IsolatedSmtLogicTest(unittest.TestCase):
    @staticmethod
    def micro(
        symbol: str,
        *,
        ts: int = 60,
        ret: float = -2.0,
        flow: float = -0.3,
        efficiency: float = 0.10,
    ) -> PeerMicroState:
        return PeerMicroState(
            symbol=symbol,
            ts_event=ts,
            ret_60s_bps=ret,
            flow_60s=flow,
            efficiency_60s=efficiency,
        )

    @staticmethod
    def session(*, confirming=()) -> SmtSessionDecision:
        peers = ("BTCUSDT", "SOLUSDT", "XRPUSDT")
        confirming = tuple(sorted(confirming))
        nonconfirming = tuple(symbol for symbol in peers if symbol not in confirming)
        return SmtSessionDecision(
            confirmed=len(nonconfirming) >= 2,
            valid_peers=peers,
            same_side_sweep_peers=confirming,
            nonconfirming_peers=nonconfirming,
        )

    def decide(self, session, micros, *, side=1, current_ts=120):
        return isolated_smt_reversal_context(
            current_symbol="ETHUSDT",
            current_ts=current_ts,
            side=side,
            session_decision=session,
            micro_states=micros,
            maximum_age_ns=65,
            minimum_counterflow=0.10,
            minimum_efficiency=0.15,
        )

    def test_every_peer_must_fail_corresponding_session_sweep(self) -> None:
        result = self.decide(
            self.session(confirming=("BTCUSDT",)),
            [self.micro("BTCUSDT"), self.micro("SOLUSDT"), self.micro("XRPUSDT")],
        )
        self.assertFalse(result.confirmed)
        self.assertEqual(result.reason_code, "SESSION_RAID_NOT_ISOLATED_ACROSS_ALL_PEERS")

    def test_two_efficient_common_continuation_peers_reject_reversal(self) -> None:
        result = self.decide(
            self.session(),
            [
                self.micro("BTCUSDT", efficiency=0.25),
                self.micro("SOLUSDT", efficiency=0.30),
                self.micro("XRPUSDT", efficiency=0.05),
            ],
        )
        self.assertFalse(result.confirmed)
        self.assertEqual(
            result.reason_code,
            "COMMON_PEER_PRICE_DISCOVERY_CONTINUES_RAID_DIRECTION",
        )
        self.assertEqual(result.common_continuation_peers, ("BTCUSDT", "SOLUSDT"))

    def test_one_efficient_peer_does_not_define_common_price_discovery(self) -> None:
        result = self.decide(
            self.session(),
            [
                self.micro("BTCUSDT", efficiency=0.20),
                self.micro("SOLUSDT", efficiency=0.08),
                self.micro("XRPUSDT", efficiency=0.10),
            ],
        )
        self.assertTrue(result.confirmed)
        self.assertEqual(result.common_continuation_peers, ("BTCUSDT",))

    def test_mirror_symmetry(self) -> None:
        long = self.decide(
            self.session(),
            [
                self.micro("BTCUSDT", ret=-2.0, flow=-0.3, efficiency=0.25),
                self.micro("SOLUSDT", ret=-1.0, flow=-0.2, efficiency=0.25),
                self.micro("XRPUSDT", ret=1.0, flow=0.2, efficiency=0.25),
            ],
            side=1,
        )
        short = self.decide(
            self.session(),
            [
                self.micro("BTCUSDT", ret=2.0, flow=0.3, efficiency=0.25),
                self.micro("SOLUSDT", ret=1.0, flow=0.2, efficiency=0.25),
                self.micro("XRPUSDT", ret=-1.0, flow=-0.2, efficiency=0.25),
            ],
            side=-1,
        )
        self.assertEqual(long.confirmed, short.confirmed)
        self.assertEqual(
            long.common_continuation_peers,
            short.common_continuation_peers,
        )

    def test_same_timestamp_and_stale_micro_states_are_excluded(self) -> None:
        result = self.decide(
            self.session(),
            [
                self.micro("BTCUSDT", ts=120),
                self.micro("SOLUSDT", ts=1),
                self.micro("XRPUSDT", ts=60),
            ],
        )
        self.assertFalse(result.confirmed)
        self.assertEqual(
            result.reason_code,
            "INSUFFICIENT_PRIOR_COMPLETED_PEER_MICRO_STATES",
        )

    def test_context_is_independent_of_same_timestamp_publish_order(self) -> None:
        context = IsolatedSmtContext()
        context.publish(self.micro("BTCUSDT", ts=60, efficiency=0.05))
        context.publish(self.micro("BTCUSDT", ts=120, efficiency=0.90))
        context.publish(self.micro("SOLUSDT", ts=60))
        states = context.prior_peer_states(current_symbol="ETHUSDT", current_ts=120)
        self.assertEqual({state.ts_event for state in states}, {60})
        btc = next(state for state in states if state.symbol == "BTCUSDT")
        self.assertEqual(btc.efficiency_60s, 0.05)


if __name__ == "__main__":
    unittest.main()
