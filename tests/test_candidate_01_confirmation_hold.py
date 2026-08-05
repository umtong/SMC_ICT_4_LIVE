from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
SRC = ROOT / "src"
for item in (CANDIDATE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import AuctionBar, CandidateConfig, Response, Side, TradePlan
from portfolio_probe import Pending, Variant, simulate


NS = 1_000_000_000


def bar(ts: int, *, close: float, high: float | None = None, low: float | None = None) -> AuctionBar:
    return AuctionBar(
        ts_event_ns=ts,
        open=close,
        high=close if high is None else high,
        low=close if low is None else low,
        close=close,
        base_volume=1.0,
        quote_volume=100.0,
        taker_buy_quote_volume=50.0,
    )


class DelayedConfirmationHoldTest(unittest.TestCase):
    def _run(self, delayed_close: float, final_high: float = 103.0):
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        first = int((start + timedelta(minutes=1)).timestamp() * NS)
        second = int((start + timedelta(minutes=2)).timestamp() * NS)
        third = int((start + timedelta(minutes=3)).timestamp() * NS)
        plan = TradePlan(
            scenario_id="hold-test",
            side=Side.LONG,
            response=Response.SWEEP_FAILURE,
            signal_time_ns=first,
            observed_time_ns=first,
            expected_entry=102.0,
            stop_price=90.0,
            target_price=110.0,
            anchor_high=110.0,
            anchor_low=90.0,
            sweep_extreme=91.0,
            atr=2.0,
            estimated_reward_risk=1.5,
            max_hold_bars=10,
            reason_code="TEST",
        )
        return simulate(
            variant=Variant("hold-test", ("BTCUSDT",), (60,)),
            bars_by_symbol={
                "BTCUSDT": [
                    bar(first, close=102.0),
                    bar(second, close=delayed_close),
                    bar(third, close=final_high, high=111.0, low=final_high),
                ],
            },
            evaluation_start=start,
            evaluation_end=start + timedelta(minutes=4),
            base_candidate=CandidateConfig(range_minutes=60),
            cost=0.0,
            minimum_price_risk_fraction=0.0,
            minimum_net_reward_risk=0.1,
            starting_nav=100_000.0,
            risk_rates=(0.03,),
            allowed_scenario_ids=frozenset(),
            external_plans_by_signal_time={
                first: (
                    Pending(
                        symbol="BTCUSDT",
                        horizon=60,
                        plan=plan,
                        confirmation_hold_price=101.0,
                    ),
                ),
            },
        )

    def test_delayed_entry_is_rejected_when_mss_hold_fails(self) -> None:
        trades, metrics, _ = self._run(100.0)
        self.assertTrue(trades.empty)
        self.assertEqual(metrics["rejections"]["failed_confirmation_hold"], 1)

    def test_delayed_entry_is_allowed_when_mss_hold_remains_valid(self) -> None:
        trades, metrics, _ = self._run(102.0, final_high=103.0)
        self.assertEqual(len(trades), 1)
        self.assertEqual(metrics["rejections"]["failed_confirmation_hold"], 0)
        self.assertEqual(float(trades.iloc[0]["confirmation_hold_price"]), 101.0)


if __name__ == "__main__":
    unittest.main()
