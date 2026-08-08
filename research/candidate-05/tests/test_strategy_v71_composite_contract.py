from __future__ import annotations

from itertools import combinations
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v55_spot_price_discovery import SpotLedPriceDiscoveryStrategy
from strategy_v56_early_flow_retrace import EarlyFlowFirstRetraceStrategy
from strategy_v58_forced_basis_reversion import ForcedBasisReversionStrategy
from strategy_v59_spot_boundary_retest import SpotBoundaryRetestStrategy
from strategy_v62_post_funding_reset import PostFundingForcedResetStrategy
from strategy_v68_liquidation_exhaustion import LiquidationExhaustionStrategy
from strategy_v70_participation_expansion import ParticipationExpansionStrategy


class SevenFamilyCompositeContractTests(unittest.TestCase):
    def test_every_nonempty_subset_has_exactly_one_v46_core(self) -> None:
        ordered = [
            ("v59", SpotBoundaryRetestStrategy),
            ("v55", SpotLedPriceDiscoveryStrategy),
            ("v70", ParticipationExpansionStrategy),
            ("v58", ForcedBasisReversionStrategy),
            ("v68", LiquidationExhaustionStrategy),
            ("v62", PostFundingForcedResetStrategy),
            ("v56", EarlyFlowFirstRetraceStrategy),
        ]
        for size in range(1, len(ordered) + 1):
            for subset in combinations(ordered, size):
                bases = tuple(candidate for _, candidate in subset)
                candidate = type("Composite", bases, {})
                self.assertTrue(issubclass(candidate, NoPostRetraceBreakawayStrategy))
                self.assertEqual(candidate.mro().count(NoPostRetraceBreakawayStrategy), 1)

    def test_full_post_bar_priority_preserves_forced_before_continuation(self) -> None:
        candidate = type(
            "FullSevenFamilyComposite",
            (
                SpotBoundaryRetestStrategy,
                SpotLedPriceDiscoveryStrategy,
                ParticipationExpansionStrategy,
                ForcedBasisReversionStrategy,
                LiquidationExhaustionStrategy,
                PostFundingForcedResetStrategy,
                EarlyFlowFirstRetraceStrategy,
            ),
            {},
        )
        mro = candidate.mro()
        self.assertLess(mro.index(ParticipationExpansionStrategy), mro.index(ForcedBasisReversionStrategy))
        self.assertLess(mro.index(ForcedBasisReversionStrategy), mro.index(LiquidationExhaustionStrategy))
        self.assertLess(mro.index(LiquidationExhaustionStrategy), mro.index(PostFundingForcedResetStrategy))
        self.assertEqual(mro.count(NoPostRetraceBreakawayStrategy), 1)


if __name__ == "__main__":
    unittest.main()
