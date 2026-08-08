from __future__ import annotations

import unittest

from strategy_v46_no_post_retrace_breakaway import NoPostRetraceBreakawayStrategy
from strategy_v55_spot_price_discovery import SpotLedPriceDiscoveryStrategy
from strategy_v56_early_flow_retrace import EarlyFlowFirstRetraceStrategy
from strategy_v58_forced_basis_reversion import ForcedBasisReversionStrategy
from strategy_v60_promoted_composite import EarlyFlowAndBasisComposite
from strategy_v60_promoted_composite import SpotAndBasisComposite
from strategy_v60_promoted_composite import SpotAndEarlyFlowComposite
from strategy_v60_promoted_composite import SpotEarlyFlowAndBasisComposite


class PromotedCompositeContractTests(unittest.TestCase):
    def test_every_composite_reaches_one_common_v46_core(self) -> None:
        for candidate in (
            SpotAndEarlyFlowComposite,
            EarlyFlowAndBasisComposite,
            SpotAndBasisComposite,
            SpotEarlyFlowAndBasisComposite,
        ):
            with self.subTest(candidate=candidate.__name__):
                self.assertTrue(issubclass(candidate, NoPostRetraceBreakawayStrategy))
                self.assertEqual(
                    candidate.mro().count(NoPostRetraceBreakawayStrategy),
                    1,
                )

    def test_three_way_mro_preserves_all_frozen_components(self) -> None:
        mro = SpotEarlyFlowAndBasisComposite.mro()
        self.assertIn(SpotLedPriceDiscoveryStrategy, mro)
        self.assertIn(ForcedBasisReversionStrategy, mro)
        self.assertIn(EarlyFlowFirstRetraceStrategy, mro)
        self.assertLess(
            mro.index(SpotLedPriceDiscoveryStrategy),
            mro.index(ForcedBasisReversionStrategy),
        )
        self.assertLess(
            mro.index(ForcedBasisReversionStrategy),
            mro.index(EarlyFlowFirstRetraceStrategy),
        )

    def test_component_classes_remain_unmodified(self) -> None:
        self.assertNotIn("on_bar", SpotEarlyFlowAndBasisComposite.__dict__)
        self.assertNotIn("_detect_sweep", SpotEarlyFlowAndBasisComposite.__dict__)
        self.assertNotIn(
            "_submit_price_capped_bracket",
            SpotEarlyFlowAndBasisComposite.__dict__,
        )


if __name__ == "__main__":
    unittest.main()
