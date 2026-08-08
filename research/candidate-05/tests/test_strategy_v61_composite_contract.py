from __future__ import annotations

from itertools import combinations
import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v55_spot_price_discovery import SpotLedPriceDiscoveryStrategy
from strategy_v56_early_flow_retrace import EarlyFlowFirstRetraceStrategy
from strategy_v58_forced_basis_reversion import ForcedBasisReversionStrategy
from strategy_v59_spot_boundary_retest import SpotBoundaryRetestStrategy


class FourFamilyCompositeContractTests(unittest.TestCase):
    def test_every_nonempty_component_subset_has_one_v46_core(self) -> None:
        ordered = [
            ("v59", SpotBoundaryRetestStrategy),
            ("v55", SpotLedPriceDiscoveryStrategy),
            ("v58", ForcedBasisReversionStrategy),
            ("v56", EarlyFlowFirstRetraceStrategy),
        ]
        for size in range(1, len(ordered) + 1):
            for subset in combinations(ordered, size):
                names = tuple(name for name, _ in subset)
                bases = tuple(candidate for _, candidate in subset)
                with self.subTest(components=names):
                    candidate = type(f"Composite_{'_'.join(names)}", bases, {})
                    self.assertTrue(
                        issubclass(candidate, NoPostRetraceBreakawayStrategy),
                    )
                    self.assertEqual(
                        candidate.mro().count(NoPostRetraceBreakawayStrategy),
                        1,
                    )

    def test_full_order_runs_parent_core_before_post_bar_observers(self) -> None:
        candidate = type(
            "FullComposite",
            (
                SpotBoundaryRetestStrategy,
                SpotLedPriceDiscoveryStrategy,
                ForcedBasisReversionStrategy,
                EarlyFlowFirstRetraceStrategy,
            ),
            {},
        )
        mro = candidate.mro()
        self.assertLess(mro.index(SpotBoundaryRetestStrategy), mro.index(SpotLedPriceDiscoveryStrategy))
        self.assertLess(mro.index(SpotLedPriceDiscoveryStrategy), mro.index(ForcedBasisReversionStrategy))
        self.assertLess(mro.index(ForcedBasisReversionStrategy), mro.index(EarlyFlowFirstRetraceStrategy))
        self.assertLess(mro.index(EarlyFlowFirstRetraceStrategy), mro.index(NoPostRetraceBreakawayStrategy))


if __name__ == "__main__":
    unittest.main()
