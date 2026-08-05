from __future__ import annotations

import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "research" / "candidate-01"
for item in (CANDIDATE, ROOT / "src"):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from aggtrade_clock import VolumeBar
from core import Side
from directional_change_failed_sweep_week import DirectionalChangeEvent
from impact_regime_probe import EventFeature
from intrinsic_external_liquidity_v2_week import (
    SweepRetestSignal,
    select_target,
)


def bar(index: int, *, close: float, high: float | None = None, low: float | None = None) -> VolumeBar:
    high_value = close if high is None else high
    low_value = close if low is None else low
    return VolumeBar(
        index=index,
        start_time_ns=index * 10 + 1,
        end_time_ns=index * 10 + 9,
        open=close,
        high=high_value,
        low=low_value,
        close=close,
        base_quantity=1.0,
        quote_notional=1000.0,
        signed_quote_notional=100.0,
        aggressive_buy_quote=550.0,
        aggressive_sell_quote=450.0,
        aggregate_trades=1,
        first_agg_trade_id=index,
        last_agg_trade_id=index,
        target_quote_notional=1000.0,
    )


def feature(item: VolumeBar) -> EventFeature:
    return EventFeature(
        bar=item,
        true_range=item.high - item.low,
        atr=1.0,
        imbalance_z=1.0,
    )


def event(*, confirmation_index: int, pivot_price: float) -> DirectionalChangeEvent:
    return DirectionalChangeEvent(
        event_type="DOWN",
        confirmation_index=confirmation_index,
        confirmation_time_ns=confirmation_index * 10 + 9,
        confirmation_price=pivot_price - 1.0,
        pivot_index=confirmation_index,
        pivot_time_ns=confirmation_index * 10 + 9,
        pivot_price=pivot_price,
        trend_start_index=0,
        trend_flow_imbalance=0.5,
        reversal_flow_imbalance=-0.5,
        path_high=pivot_price,
        path_low=pivot_price - 2.0,
    )


class ExternalTargetRoutingTests(unittest.TestCase):
    def signal(self) -> SweepRetestSignal:
        return SweepRetestSignal(
            scenario_id="signal",
            side=Side.LONG,
            signal_bar_index=1,
            signal_time_ns=19,
            boundary=99.5,
            stop_price=99.0,
            path_high=100.5,
            path_low=98.5,
            trend_flow_imbalance=-0.5,
            reversal_flow_imbalance=0.5,
        )

    def test_future_confirmed_target_is_ignored(self) -> None:
        features = [
            feature(bar(0, close=100.0, high=100.5, low=99.5)),
            feature(bar(1, close=100.0, high=101.0, low=99.5)),
            feature(bar(2, close=100.0, high=101.0, low=99.5)),
        ]
        plan, target_index, _, _ = select_target(
            signal=self.signal(),
            features=features,
            events=[
                event(confirmation_index=0, pivot_price=105.0),
                event(confirmation_index=2, pivot_price=102.0),
            ],
            cost=0.0007,
            minimum_price_risk_fraction=0.65,
            minimum_net_reward_risk=1.35,
        )
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(target_index, 0)
        self.assertEqual(plan.target_price, 105.0)

    def test_consumed_confirmed_target_is_rejected(self) -> None:
        features = [
            feature(bar(0, close=100.0, high=100.5, low=99.5)),
            feature(bar(1, close=100.0, high=105.1, low=99.5)),
        ]
        plan, target_index, price_fraction, net_rr = select_target(
            signal=self.signal(),
            features=features,
            events=[event(confirmation_index=0, pivot_price=105.0)],
            cost=0.0007,
            minimum_price_risk_fraction=0.65,
            minimum_net_reward_risk=1.35,
        )
        self.assertIsNone(plan)
        self.assertIsNone(target_index)
        self.assertIsNone(price_fraction)
        self.assertIsNone(net_rr)

    def test_signal_close_cost_geometry_is_enforced(self) -> None:
        tight = SweepRetestSignal(
            scenario_id="tight",
            side=Side.LONG,
            signal_bar_index=1,
            signal_time_ns=19,
            boundary=99.99,
            stop_price=99.95,
            path_high=100.1,
            path_low=99.9,
            trend_flow_imbalance=-0.5,
            reversal_flow_imbalance=0.5,
        )
        features = [
            feature(bar(0, close=100.0, high=100.1, low=99.9)),
            feature(bar(1, close=100.0, high=100.1, low=99.9)),
        ]
        plan, _, _, _ = select_target(
            signal=tight,
            features=features,
            events=[event(confirmation_index=0, pivot_price=110.0)],
            cost=0.0007,
            minimum_price_risk_fraction=0.65,
            minimum_net_reward_risk=1.35,
        )
        self.assertIsNone(plan)


if __name__ == "__main__":
    unittest.main()
