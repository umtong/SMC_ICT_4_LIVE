from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "rich_signal_compiler_v27.py"
SPEC = importlib.util.spec_from_file_location("candidate04_v27_test", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot load {MODULE_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class _Intent:
    def __init__(self, scenario: str) -> None:
        self.scenario = scenario


class V27RouterTests(unittest.TestCase):
    def test_orderly_inventory_is_rejected_from_v26c(self) -> None:
        self.assertFalse(
            MODULE.admitted_v26c(_Intent("ORDERLY_INVENTORY_DISPLACEMENT"))
        )
        self.assertTrue(
            MODULE.admitted_v26c(_Intent("NORMAL_FAILED_AUCTION_RESUMPTION"))
        )

    def test_only_positive_complementary_branches_are_admitted(self) -> None:
        self.assertIn(
            MODULE.failed_break.LIQUIDATION_SCENARIO,
            MODULE.ADMITTED_COMPLEMENTARY_SCENARIOS,
        )
        self.assertIn(
            MODULE.balanced_session.FAILED_INVENTORY_SCENARIO,
            MODULE.ADMITTED_COMPLEMENTARY_SCENARIOS,
        )
        self.assertIn(
            MODULE.directional_session.LIQUIDATION_SCENARIO,
            MODULE.ADMITTED_COMPLEMENTARY_SCENARIOS,
        )
        self.assertNotIn(
            MODULE.failed_break.FRESH_SCENARIO,
            MODULE.ADMITTED_COMPLEMENTARY_SCENARIOS,
        )

    def test_specific_failed_break_chain_has_priority(self) -> None:
        self.assertLess(
            MODULE._priority(MODULE.failed_break.LIQUIDATION_SCENARIO),
            MODULE._priority("NORMAL_FAILED_AUCTION_RESUMPTION"),
        )


if __name__ == "__main__":
    unittest.main()
