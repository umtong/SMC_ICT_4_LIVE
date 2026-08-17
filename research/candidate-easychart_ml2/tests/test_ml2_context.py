from __future__ import annotations

from types import SimpleNamespace
import unittest

from ml2_context import (
    FactorTransitionBook,
    inherited_preplan_factor_allows,
    plan_factor_snapshots,
)


class Side:
    def __init__(self, name: str) -> None:
        self.name = name


def _state(name: str):
    return SimpleNamespace(side=Side(name), event_time_ns=0, agreeing_symbols=())


def _plan(**updates):
    values = dict(
        family="MICRO_5M_ACCEPTED_BREAK_FIRST_EFFICIENT_PULLBACK",
        scale_name="EFFICIENT_PULLBACK",
        scenario_path="ACCEPTANCE",
        higher_zone_kind="HORIZONTAL_SUPPORT",
        lower_zone_kind="ORDER_BLOCK",
        trigger_zone_kind="FIRST_RESPONSE_ALIGNED_INITIATIVE",
        target_zone_kind="SWING_HIGH",
        rule_provenance=(),
        side=Side("LONG"),
        setup_observed_time_ns=5 * 60_000_000_000,
        observed_time_ns=12 * 60_000_000_000,
        trigger_timeframe_minutes=1,
    )
    values.update(updates)
    return SimpleNamespace(**values)


class ContextTest(unittest.TestCase):
    def test_transition_book_is_causal_piecewise_constant(self) -> None:
        book = FactorTransitionBook()
        long = _state("LONG")
        short = _state("SHORT")
        book.observe(2, None)
        book.observe(3, long)
        book.observe(4, long)
        book.observe(8, short)
        book.observe(10, None)
        self.assertEqual(book.transitions, 4)
        self.assertIsNone(book.state_at(1))
        self.assertIs(book.state_at(6), long)
        self.assertIs(book.state_at(9), short)
        self.assertIsNone(book.state_at(10))
        with self.assertRaises(RuntimeError):
            book.observe(9, long)

    def test_shadow_reconstructs_only_the_specific_hidden_veto(self) -> None:
        minute = 60_000_000_000
        book = FactorTransitionBook()
        book.observe(0, None)
        book.observe(4 * minute, _state("SHORT"))
        book.observe(7 * minute, None)
        book.observe(11 * minute, _state("LONG"))
        efficient = _plan()
        setup, pre_response = plan_factor_snapshots(efficient, book)
        self.assertEqual(setup.side.name, "SHORT")
        self.assertEqual(pre_response.side.name, "LONG")
        self.assertTrue(inherited_preplan_factor_allows(efficient, book))
        local_ob = _plan(
            family="FACTOR_CONTINUATION_5M_OB_FIRST_RETURN",
            scale_name="LOCAL_AUCTION_CONTINUATION",
        )
        self.assertFalse(inherited_preplan_factor_allows(local_ob, book))

    def test_global_provenance_does_not_reclassify_a_rejection(self) -> None:
        book = FactorTransitionBook()
        book.observe(0, _state("SHORT"))
        rejection = _plan(
            family="STRUCTURE_REJECTION_FOOTPRINT_RETEST",
            scale_name="REJECTION",
            scenario_path="REJECTION",
            rule_provenance=("LOCAL_AUCTION_CONTINUATION", "EFFICIENT_PULLBACK"),
        )
        self.assertTrue(inherited_preplan_factor_allows(rejection, book))


if __name__ == "__main__":
    unittest.main()
