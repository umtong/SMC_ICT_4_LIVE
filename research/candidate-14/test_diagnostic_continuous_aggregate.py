from __future__ import annotations

import sys
import unittest

import diagnostic_continuous_aggregate as target


class DiagnosticAggregateLoadTest(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop(target.MODULE_NAME, None)

    def test_load_and_unload(self) -> None:
        module = target.load_materialized_module()
        try:
            self.assertIs(sys.modules[target.MODULE_NAME], module)
            self.assertTrue(callable(module.__dict__.get("main")))
            row_type = module.__dict__.get("ClosedTrade")
            self.assertIsNotNone(row_type)
            self.assertEqual(row_type.__module__, target.MODULE_NAME)
        finally:
            target.unload_materialized_module(module)
        self.assertNotIn(target.MODULE_NAME, sys.modules)


if __name__ == "__main__":
    unittest.main()
