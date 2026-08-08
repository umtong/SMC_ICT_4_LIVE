from __future__ import annotations

import unittest

from cross_impact_context import CrossImpactObservation
from cross_impact_context import LaggedCrossImpactContext


MINUTE = 60 * 1_000_000_000


def state(
    symbol: str,
    ts: int,
    *,
    flow: float,
    ret: float,
    tail: float | None = None,
    minute: float | None = None,
    burst: float = 1.1,
    depth: float = 0.1,
) -> CrossImpactObservation:
    return CrossImpactObservation(
        symbol=symbol,
        ts_event=ts,
        flow_15s=flow if tail is None else tail,
        flow_60s=flow if minute is None else minute,
        flow_3m=flow,
        ret_atr=ret,
        efficiency_60s=0.5,
        notional_burst=burst,
        depth_imbalance_1=depth,
    )


class CrossImpactContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.context = LaggedCrossImpactContext()
        self.context.ensure_run((1, 2))
        self.shock_ts = 10_000 * MINUTE
        for symbol in ("ETHUSDT", "SOLUSDT", "XRPUSDT"):
            for offset in range(61, 0, -1):
                signed = 0.015 if offset % 2 else -0.012
                self.context.publish(
                    state(symbol, self.shock_ts - offset * MINUTE, flow=signed, ret=0.01),
                )
        for offset in range(61, 0, -1):
            self.context.publish(
                state("BTCUSDT", self.shock_ts - offset * MINUTE, flow=0.01, ret=0.0),
            )

        self.context.publish(state("ETHUSDT", self.shock_ts, flow=0.42, ret=0.26))
        self.context.publish(state("SOLUSDT", self.shock_ts, flow=0.36, ret=0.22))
        self.context.publish(state("XRPUSDT", self.shock_ts, flow=-0.01, ret=0.0))
        self.context.publish(state("BTCUSDT", self.shock_ts, flow=0.02, ret=0.02))

    def current(self, *, ret: float = 0.05) -> CrossImpactObservation:
        return state(
            "BTCUSDT",
            self.shock_ts + MINUTE,
            flow=0.10,
            ret=ret,
            tail=0.24,
            minute=0.11,
            burst=1.25,
            depth=0.08,
        )

    def test_prior_multi_peer_flow_shock_creates_long_lag_decision(self) -> None:
        decision = self.context.decide(
            target_symbol="BTCUSDT",
            current=self.current(),
        )
        self.assertTrue(decision.actionable)
        self.assertEqual(decision.side, 1)
        self.assertEqual(set(decision.peer_symbols), {"ETHUSDT", "SOLUSDT"})
        self.assertGreater(decision.remaining_lag_gap_atr, 0.04)

    def test_equal_timestamp_peer_state_is_never_consumed(self) -> None:
        current = self.current()
        self.context.publish(
            state("ETHUSDT", current.ts_event, flow=-0.95, ret=-1.0),
        )
        decision = self.context.decide(target_symbol="BTCUSDT", current=current)
        self.assertTrue(decision.actionable)
        self.assertEqual(decision.side, 1)

    def test_signal_is_rejected_after_target_consumes_lag(self) -> None:
        decision = self.context.decide(
            target_symbol="BTCUSDT",
            current=self.current(ret=0.30),
        )
        self.assertFalse(decision.actionable)
        self.assertEqual(decision.reason, "LAG_ALREADY_CONSUMED")

    def test_peer_disagreement_is_no_trade(self) -> None:
        other = LaggedCrossImpactContext()
        other.ensure_run((3, 4))
        shock = self.shock_ts
        for symbol in ("ETHUSDT", "SOLUSDT", "XRPUSDT", "BTCUSDT"):
            for offset in range(61, 0, -1):
                other.publish(state(symbol, shock - offset * MINUTE, flow=0.01, ret=0.0))
        other.publish(state("ETHUSDT", shock, flow=0.5, ret=0.3))
        other.publish(state("SOLUSDT", shock, flow=-0.5, ret=-0.3))
        other.publish(state("XRPUSDT", shock, flow=0.01, ret=0.0))
        other.publish(state("BTCUSDT", shock, flow=0.01, ret=0.0))
        decision = other.decide(target_symbol="BTCUSDT", current=self.current())
        self.assertFalse(decision.actionable)
        self.assertIn(
            decision.reason,
            {"NO_MULTI_PEER_FLOW_SHOCK", "PEER_FLOW_SHOCK_DISAGREEMENT"},
        )


if __name__ == "__main__":
    unittest.main()
