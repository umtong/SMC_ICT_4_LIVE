from __future__ import annotations

import math
import unittest
from pathlib import Path

from derive_nt_lvcfr_v18_signals import (
    BlockAccumulator,
    CandidateBlocks,
    L1Features,
    L1_ABSORPTION_FULL_RANGE_CHOCH_REVERSAL,
    L1_VACUUM_CONTINUATION,
    Quote,
    classify_resilience,
)


def feature(**overrides):
    values = dict(
        updates=20,
        progress_bp=0.5,
        microprice_bp=0.2,
        directional_ofi_norm=1.0,
        gross_flow_norm=2.0,
        impact_efficiency=0.5,
        opposing_replenishment_norm=0.5,
        opposing_depletion_norm=0.5,
        opposing_depth_ratio=1.0,
        spread_end_bp=2.0,
        low_mid=99.0,
        high_mid=101.0,
        last_mid=100.0,
    )
    values.update(overrides)
    return L1Features(**values)


class V18Tests(unittest.TestCase):
    def test_vacuum_requires_high_impact_and_weak_opposing_replenishment(self):
        baseline = [feature() for _ in range(20)]
        observation = feature(
            progress_bp=2.0,
            microprice_bp=1.0,
            directional_ofi_norm=0.5,
            gross_flow_norm=0.5,
            impact_efficiency=4.0,
            opposing_replenishment_norm=0.1,
            opposing_depletion_norm=1.0,
            opposing_depth_ratio=0.5,
            spread_end_bp=1.5,
        )
        self.assertEqual(
            classify_resilience(baseline, observation),
            L1_VACUUM_CONTINUATION,
        )

    def test_absorption_requires_pressure_without_progress_and_replenishment(self):
        baseline = [feature() for _ in range(20)]
        observation = feature(
            progress_bp=-1.0,
            microprice_bp=-0.5,
            directional_ofi_norm=3.0,
            impact_efficiency=-0.3,
            opposing_replenishment_norm=2.0,
            opposing_depletion_norm=0.1,
            opposing_depth_ratio=2.0,
            spread_end_bp=1.5,
        )
        self.assertEqual(
            classify_resilience(baseline, observation),
            L1_ABSORPTION_FULL_RANGE_CHOCH_REVERSAL,
        )

    def test_mixed_response_is_no_trade(self):
        baseline = [feature() for _ in range(20)]
        observation = feature(
            progress_bp=0.1,
            microprice_bp=0.1,
            directional_ofi_norm=1.1,
            impact_efficiency=0.1,
            opposing_replenishment_norm=0.6,
            opposing_depth_ratio=1.1,
        )
        self.assertIsNone(classify_resilience(baseline, observation))

    def test_standard_ofi_detects_ask_depletion_as_upward_pressure(self):
        block = BlockAccumulator(direction=1)
        first = Quote(1, 1, 100.0, 5.0, 100.1, 5.0)
        second = Quote(2, 2, 100.0, 5.0, 100.2, 4.0)
        block.update(first, second)
        result = block.features()
        self.assertIsNone(result)  # fewer than the fixed minimum updates
        for index in range(3, 24):
            previous = second
            second = Quote(
                index,
                index,
                100.0,
                5.0,
                100.2 + 0.1 * (index - 2),
                4.0,
            )
            block.update(previous, second)
        result = block.features()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertGreater(result.directional_ofi_norm, 0.0)
        self.assertGreater(result.opposing_depletion_norm, 0.0)

    def test_baseline_is_pre_event_and_event_gap_only_advances_reference_quote(self):
        minute = 60_000_000_000
        second = 1_000_000_000
        context = CandidateBlocks(
            signal={"direction": 1},
            start_ns=0,
            baseline_end_ns=10 * minute,
            confirm_ns=20 * minute,
            end_ns=20 * minute + 30 * second,
        )
        # Complete one pre-event baseline block.
        for index in range(22):
            context.consume(
                Quote(
                    index * second,
                    index * second,
                    100.0 + 0.001 * index,
                    5.0,
                    100.1 + 0.001 * index,
                    5.0,
                )
            )
        baseline_updates = context.blocks[0].updates
        # Expansion-event quotes must not enter any baseline feature block, but
        # the latest one must become the reference for post-event OFI.
        context.consume(
            Quote(15 * minute, 15 * minute, 199.9, 5.0, 200.1, 5.0)
        )
        context.consume(
            Quote(
                20 * minute - second,
                20 * minute - second,
                200.0,
                5.0,
                200.2,
                5.0,
            )
        )
        self.assertEqual(context.blocks[0].updates, baseline_updates)
        for index in range(21):
            timestamp = 20 * minute + index * second
            context.consume(
                Quote(
                    timestamp,
                    timestamp,
                    200.0 + 0.01 * index,
                    5.0,
                    200.2 + 0.01 * index,
                    5.0,
                )
            )
        observation = context.blocks[-1].features()
        self.assertIsNotNone(observation)
        assert observation is not None
        # A contaminated baseline-to-observation jump would be near +100%; the
        # correct reference is the immediately preceding event-gap quote.
        self.assertLess(abs(observation.progress_bp), 100.0)
        self.assertGreaterEqual(observation.updates, 20)

    def test_impact_efficiency_uses_gross_not_nearly_cancelling_net_flow(self):
        block = BlockAccumulator(direction=1)
        previous = Quote(0, 0, 100.0, 10.0, 100.1, 10.0)
        for index in range(1, 22):
            current = Quote(
                index,
                index,
                100.0 + 0.001 * index,
                10.0 + (index % 2),
                100.1 + 0.001 * index,
                10.0 + ((index + 1) % 2),
            )
            block.update(previous, current)
            previous = current
        result = block.features()
        self.assertIsNotNone(result)
        assert result is not None
        self.assertGreater(result.gross_flow_norm, 0.0)
        self.assertTrue(math.isfinite(result.impact_efficiency))

    def test_detector_contains_no_execution_or_nav_engine(self):
        source = Path(__file__).with_name(
            "derive_nt_lvcfr_v18_signals.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("BacktestNode", source)
        self.assertNotIn("simulate_fill", source)
        self.assertNotIn("synthetic_nav", source)
        self.assertIn("candidate-local causal quartiles", source)
        self.assertIn("PRE_EVENT_NOT_EXPANSION_EVENT", source)
        self.assertNotIn("progress / max(abs(ofi_norm)", source)
        self.assertIn("progress / gross_flow_norm", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
