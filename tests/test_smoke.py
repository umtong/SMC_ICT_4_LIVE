from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


@unittest.skipUnless(importlib.util.find_spec("nautilus_trader"), "NautilusTrader is not installed")
class NautilusSmokeTests(unittest.TestCase):
    def test_real_engine_order_cycle(self):
        from smc_ict_4.smoke import run_smoke

        with tempfile.TemporaryDirectory() as directory:
            metrics = run_smoke(Path(directory))
            self.assertGreaterEqual(metrics["fills"], 2)
            self.assertGreaterEqual(metrics["positions"], 1)


if __name__ == "__main__":
    unittest.main()
