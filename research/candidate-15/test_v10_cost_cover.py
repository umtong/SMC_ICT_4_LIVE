from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unittest

from candidate15_portfolio_materializer import materialize_candidate15_portfolio_source
from candidate15_v10_cost_cover_materializer import materialize_execution_valid_cost_cover_source
from candidate15_v6_residual_laggard_materializer import materialize_residual_laggard_source
from candidate15_v7_bounded_transfer_materializer import materialize_bounded_transfer_source
from candidate15_v8_managed_transfer_materializer import materialize_managed_transfer_source
from candidate15_v9_beta_transfer_materializer import materialize_beta_coherent_transfer_source
from portfolio_materializer import materialize_combined_portfolio_source
from positive_cost_cover import positive_cost_cover_trigger
from runner_materializer import materialize_runner_source


class PositiveCostCoverTests(unittest.TestCase):
    def test_e01_actual_short_fill_is_positive_after_two_tick_slippage(self) -> None:
        quote = positive_cost_cover_trigger(
            direction="SHORT",
            actual_average_entry=Decimal("32.41596936801022"),
            price_increment=Decimal("0.001"),
            entry_fee_rate=Decimal("0.0004"),
            exit_fee_rate=Decimal("0.0008"),
            adverse_slippage_ticks=2,
            minimum_net_ticks=1,
        )
        self.assertEqual(quote.trigger_price, Decimal("32.374"))
        self.assertEqual(quote.expected_adverse_fill, Decimal("32.376"))
        self.assertGreaterEqual(quote.expected_net_gain_per_unit, Decimal("0.001"))

    def test_long_trigger_budgets_fees_slippage_and_one_net_tick(self) -> None:
        quote = positive_cost_cover_trigger(
            direction="LONG",
            actual_average_entry=Decimal("3000.017"),
            price_increment=Decimal("0.01"),
            entry_fee_rate=Decimal("0.0004"),
            exit_fee_rate=Decimal("0.0008"),
            adverse_slippage_ticks=2,
            minimum_net_ticks=1,
        )
        adverse = quote.trigger_price - Decimal("0.02")
        net = adverse * Decimal("0.9992") - Decimal("3000.017") * Decimal("1.0004")
        self.assertEqual(adverse, quote.expected_adverse_fill)
        self.assertGreaterEqual(net, Decimal("0.01"))

    def test_full_v10_runner_compiles(self) -> None:
        root = Path(__file__).resolve().parent
        candidate14 = root.parent / "candidate-14"
        source = (candidate14 / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
        for transform in (
            materialize_runner_source,
            materialize_combined_portfolio_source,
            materialize_candidate15_portfolio_source,
            materialize_residual_laggard_source,
            materialize_bounded_transfer_source,
            materialize_managed_transfer_source,
            materialize_beta_coherent_transfer_source,
            materialize_execution_valid_cost_cover_source,
        ):
            source = transform(source)
        compile(source, str(candidate14 / "run_leadership_scdam_base.py"), "exec")
        self.assertEqual(source.count("positive_cost_cover_trigger("), 1)
        self.assertEqual(source.count("position.avg_px_open"), 1)
        self.assertNotIn("candidate-15-v9-strict-open-time", source)


if __name__ == "__main__":
    unittest.main()
