"""Regression test for v24 impact/cost class installation order."""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest


class V24InstallOrderTests(unittest.TestCase):
    def test_dense_launcher_freezes_impact_strategy_before_live_ledger(self) -> None:
        script = Path(__file__).resolve().with_name("run_v24_dense.py")
        code = f"""
import importlib.util
spec = importlib.util.spec_from_file_location('run_v24_dense_contract', {str(script)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from c10_v24_strategy import CrossMarketCandidate10Strategy
names = [item.__name__ for item in CrossMarketCandidate10Strategy.__mro__]
assert 'LiveCostLiquidationStrategy' in names, names
assert 'ImpactControlledLiquidationStrategy' in names, names
assert names.index('LiveCostLiquidationStrategy') < names.index('ImpactControlledLiquidationStrategy'), names
print('|'.join(names))
"""
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=(completed.stdout + "\n" + completed.stderr),
        )
        self.assertIn("ImpactControlledLiquidationStrategy", completed.stdout)


if __name__ == "__main__":
    unittest.main()
