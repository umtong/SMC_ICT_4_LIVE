from __future__ import annotations

import unittest

from hierarchical_sweep_engine import _AuctionBar
from lrb_types import BarObservation, PrimitiveSnapshot
from sequential_impact_persistence_engine import SequentialImpactPersistenceRelayEngine


def auction(
    end: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float,
    flow: float,
) -> _AuctionBar:
    return _AuctionBar(
        end - 1,
        end,
        open_,
        high,
        low,
        close,
        volume,
        volume * (flow + 1.0) / 2.0,
        100,
    )


def snap(
    index: int,
    timestamp: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    flow: float,
) -> PrimitiveSnapshot:
    width = max(high - low, 0.1)
    volume = 100.0
    return PrimitiveSnapshot(
        index=index,
        observation=BarObservation(
            timestamp,
            open_,
            high,
            low,
            close,
            volume,
            volume * (flow + 1.0) / 2.0,
            10,
        ),
        ready=True,
        atr=1.0,
        rel_volume=1.0,
        flow_ratio=flow,
        body_atr=abs(close - open_),
        range_atr=width,
        upper_wick_fraction=max(high - max(open_, close), 0.0) / width,
        lower_wick_fraction=max(min(open_, close) - low, 0.0) / width,
        close_location=(close - low) / width,
        upper_fast=150.0,
        lower_fast=70.0,
        upper_slow=160.0,
        lower_slow=60.0,
        slow_mid=105.0,
        range_position=0.5,
        upper_pool_touches=2,
        lower_pool_touches=2,
    )


class SequentialImpactPersistenceTests(unittest.TestCase):
    def params(self, **overrides):
        values = {
            "hsc_bias_period_minutes": 15,
            "hsc_liquidity_period_minutes": 5,
            "hsc_bias_atr_bars": 8,
            "hsc_bias_volume_bars": 8,
            "hsc_bias_breakout_lookback": 4,
            "hsc_bias_acceptance_close_atr": 0.0,
            "hsc_bias_range_atr": 0.50,
            "hsc_bias_body_fraction": 0.40,
            "hsc_bias_relative_volume": 0.50,
            "hsc_bias_flow_ratio": 0.01,
            "hsc_bias_close_location": 0.60,
            "hsp_bias_expiry_mode": "STRUCTURAL_ONLY",
            "hsc_bias_boundary_loss_atr": 0.08,
            "hsc_use_flow_proxy": True,
            "hff_use_bias_flow": False,
            "hff_use_sweep_flow": True,
            "hff_use_response_flow": True,
            "afhr_use_adaptive_quality": False,
            "afhr_use_extreme_freshness": True,
            "afhr_stale_periods": 6.0,
            "siar_use_flow_surprise": False,
            "siar_use_impact_efficiency": True,
            "siar_flow_lookback": 8,
            "siar_min_history": 8,
            "siar_surprise_quantile": 0.75,
            "siar_min_efficiency_history": 4,
            "sipr_use_sequential_acceptance": True,
            "sipr_use_impact_efficiency": True,
        }
        values.update(overrides)
        return values

    def seeded(self, **overrides):
        engine = SequentialImpactPersistenceRelayEngine(self.params(**overrides))
        flows = [-0.4, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, -0.3]
        closes = [98.0, 99.0, 100.0, 100.0, 101.0, 102.5, 103.5, 97.5]
        engine._bias_history = [
            auction(
                index + 1,
                100.0,
                max(104.0, close + 1.0),
                min(96.0, close - 1.0),
                close,
                200.0,
                flow,
            )
            for index, (flow, close) in enumerate(zip(flows, closes))
        ]
        engine._bias_true_ranges = [10.0] * 8
        engine._bias_volumes = [200.0] * 8
        return engine

    @staticmethod
    def first_bar() -> _AuctionBar:
        return auction(100, 104.0, 122.0, 104.0, 120.0, 320.0, 0.9)

    @staticmethod
    def second_bar() -> _AuctionBar:
        return auction(101, 120.0, 142.0, 120.0, 140.0, 340.0, 0.95)

    def test_first_effective_auction_only_arms_sequence(self) -> None:
        engine = self.seeded()
        first = self.first_bar()
        transitions = engine._evaluate_completed_bias(
            first,
            snap(1000, 100, 104.0, 122.0, 104.0, 120.0, 0.9),
        )
        self.assertTrue(transitions)
        self.assertIsNotNone(engine._persistence_candidate)
        self.assertIsNone(engine._bias)
        self.assertEqual(transitions[-1].next_state, "FIRST_ACCEPTANCE")

    def test_consecutive_effective_auction_creates_directional_context(self) -> None:
        engine = self.seeded()
        first = self.first_bar()
        engine._evaluate_completed_bias(
            first,
            snap(1000, 100, 104.0, 122.0, 104.0, 120.0, 0.9),
        )
        engine._append_bias_history(first)
        second = self.second_bar()
        transitions = engine._evaluate_completed_bias(
            second,
            snap(1015, 101, 120.0, 142.0, 120.0, 140.0, 0.95),
        )
        self.assertTrue(transitions)
        self.assertIsNone(engine._persistence_candidate)
        self.assertIsNotNone(engine._bias)
        assert engine._bias is not None
        self.assertEqual(engine._bias.direction, "LONG")
        self.assertIn(engine._bias.context_id, engine._sipr_by_context)
        reasons = {transition.reason_code for transition in transitions}
        self.assertIn("CONSECUTIVE_EFFECTIVE_AUCTIONS_CONFIRMED", reasons)
        self.assertIn("COMPLETED_HIGHER_TIMEFRAME_RANGE_ACCEPTED", reasons)

    def test_next_nonpersistent_auction_resets_without_bias(self) -> None:
        engine = self.seeded(sipr_use_impact_efficiency=False)
        first = self.first_bar()
        engine._evaluate_completed_bias(
            first,
            snap(1000, 100, 104.0, 122.0, 104.0, 120.0, 0.9),
        )
        engine._append_bias_history(first)
        neutral = auction(101, 120.0, 121.0, 115.0, 117.0, 180.0, -0.1)
        transitions = engine._evaluate_completed_bias(
            neutral,
            snap(1015, 101, 120.0, 121.0, 115.0, 117.0, -0.1),
        )
        self.assertIsNone(engine._persistence_candidate)
        self.assertIsNone(engine._bias)
        self.assertEqual(len(transitions), 1)
        self.assertEqual(
            transitions[0].reason_code,
            "NEXT_AUCTION_DID_NOT_PERSIST_IN_FIRST_DIRECTION",
        )

    def test_impact_only_ablation_activates_on_first_effective_auction(self) -> None:
        engine = self.seeded(sipr_use_sequential_acceptance=False)
        first = self.first_bar()
        transitions = engine._evaluate_completed_bias(
            first,
            snap(1000, 100, 104.0, 122.0, 104.0, 120.0, 0.9),
        )
        self.assertTrue(transitions)
        self.assertIsNone(engine._persistence_candidate)
        self.assertIsNotNone(engine._bias)

    def test_impact_factor_isolated_from_raw_structural_acceptance(self) -> None:
        inefficient = auction(100, 102.5, 105.0, 100.0, 104.8, 300.0, 0.8)

        impact = self.seeded(sipr_use_sequential_acceptance=False)
        impact._evaluate_completed_bias(
            inefficient,
            snap(1000, 100, 102.5, 105.0, 100.0, 104.8, 0.8),
        )
        self.assertIsNone(impact._bias)

        raw = self.seeded(
            sipr_use_sequential_acceptance=False,
            sipr_use_impact_efficiency=False,
        )
        raw._evaluate_completed_bias(
            inefficient,
            snap(1000, 100, 102.5, 105.0, 100.0, 104.8, 0.8),
        )
        self.assertIsNotNone(raw._bias)


if __name__ == "__main__":
    unittest.main()
