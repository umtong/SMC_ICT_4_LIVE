from __future__ import annotations

import sys
import unittest

import v6_continuous_aggregate as v6


class V6ContinuousAggregateMaterializerTests(unittest.TestCase):
    def tearDown(self) -> None:
        sys.modules.pop(v6.MODULE_NAME, None)

    def test_materialized_evaluator_loads_as_real_module(self) -> None:
        module = v6.load_materialized_module()
        try:
            self.assertIs(sys.modules[v6.MODULE_NAME], module)
            self.assertTrue(callable(module.__dict__.get("evaluate")))
            self.assertTrue(callable(module.__dict__.get("main")))
            closed_trade = module.__dict__.get("ClosedTrade")
            self.assertIsNotNone(closed_trade)
            self.assertEqual(closed_trade.__module__, v6.MODULE_NAME)
        finally:
            v6.unload_materialized_module(module)
        self.assertNotIn(v6.MODULE_NAME, sys.modules)

    def test_double_load_fails_closed(self) -> None:
        module = v6.load_materialized_module()
        try:
            with self.assertRaises(RuntimeError):
                v6.load_materialized_module()
        finally:
            v6.unload_materialized_module(module)


if __name__ == "__main__":
    unittest.main()
