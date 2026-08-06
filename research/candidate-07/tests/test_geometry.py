from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import unittest

CANDIDATE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CANDIDATE_DIR))

from geometry import adjust_target_for_submission  # noqa: E402
from model import Direction, ScenarioKind  # noqa: E402


class SubmissionGeometryTests(unittest.TestCase):
    def test_long_reversal_caps_target_from_actual_entry(self) -> None:
        result = adjust_target_for_submission(
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=Direction.LONG,
            entry_reference=Decimal("100"),
            stop=Decimal("98"),
            structural_target=Decimal("110"),
            maximum_reversal_rr=Decimal("3"),
            continuation_rr=Decimal("2.2"),
        )
        self.assertEqual(result.target, Decimal("106"))
        self.assertEqual(result.expected_rr, Decimal("3"))
        self.assertTrue(result.target_was_clamped)

    def test_short_reversal_keeps_nearer_structural_liquidity(self) -> None:
        result = adjust_target_for_submission(
            kind=ScenarioKind.ABSORPTION_RECLAIM,
            direction=Direction.SHORT,
            entry_reference=Decimal("100"),
            stop=Decimal("102"),
            structural_target=Decimal("96"),
            maximum_reversal_rr=Decimal("3"),
            continuation_rr=Decimal("2.2"),
        )
        self.assertEqual(result.target, Decimal("96"))
        self.assertEqual(result.expected_rr, Decimal("2"))
        self.assertFalse(result.target_was_clamped)

    def test_continuation_is_remeasured_to_configured_rr(self) -> None:
        result = adjust_target_for_submission(
            kind=ScenarioKind.ACCEPTANCE_CONTINUATION,
            direction=Direction.LONG,
            entry_reference=Decimal("100"),
            stop=Decimal("99"),
            structural_target=Decimal("105"),
            maximum_reversal_rr=Decimal("3"),
            continuation_rr=Decimal("2.2"),
        )
        self.assertEqual(result.target, Decimal("102.2"))
        self.assertEqual(result.expected_rr, Decimal("2.2"))
        self.assertTrue(result.target_was_clamped)

    def test_untradeable_delayed_entry_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            adjust_target_for_submission(
                kind=ScenarioKind.ABSORPTION_RECLAIM,
                direction=Direction.LONG,
                entry_reference=Decimal("100"),
                stop=Decimal("101"),
                structural_target=Decimal("105"),
                maximum_reversal_rr=Decimal("3"),
                continuation_rr=Decimal("2.2"),
            )


if __name__ == "__main__":
    unittest.main()
