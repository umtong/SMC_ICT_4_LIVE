"""Source-stable runner contracts for auction-router implementation refinement v2."""

from __future__ import annotations

import unittest

import run_aggtrade_auction_router_nautilus_v2 as v2_runner
from aggtrade_auction_router_signals_v2 import (
    IMPLEMENTATION_REVISION,
    build_auction_router_signals,
)


class AuctionRunnerV2Contracts(unittest.TestCase):
    def test_v2_detector_is_the_base_runner_entrypoint(self) -> None:
        self.assertIs(
            v2_runner.runner.build_auction_router_signals,
            build_auction_router_signals,
        )
        self.assertIs(
            v2_runner.runner.base_runner.build_acceptance_signals,
            v2_runner.runner._build_router_signals,
        )

    def test_suite_summary_is_stamped_with_exact_implementation_revision(self) -> None:
        original = v2_runner._original_suite_summary
        try:
            v2_runner._original_suite_summary = lambda *_args: {
                "suite_gate_passed": False,
                "auction_family_mode": "both",
            }
            summary = v2_runner._v2_suite_summary({}, "first", [])
        finally:
            v2_runner._original_suite_summary = original

        self.assertEqual(summary["implementation_revision"], IMPLEMENTATION_REVISION)
        self.assertFalse(summary["suite_gate_passed"])
        self.assertEqual(summary["auction_family_mode"], "both")

    def test_base_runner_uses_revision_stamped_suite_summary(self) -> None:
        self.assertIs(
            v2_runner.runner.base_runner._suite_summary,
            v2_runner._v2_suite_summary,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
