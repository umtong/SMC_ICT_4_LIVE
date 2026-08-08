from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


class Candidate35ContractTest(unittest.TestCase):
    def test_fixed_universe_and_risk_budget(self) -> None:
        config = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(
            config["symbols"],
            ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        )
        self.assertEqual(config["risk_fraction"], 0.03)
        self.assertGreater(config["all_in_cost_bps_each_side"], 0.0)
        self.assertGreater(config["adverse_slippage_bps_each_side"], 0.0)
        self.assertFalse(config["strategy"]["allow_reversal"])

    def test_strategy_uses_nautilus_and_has_no_custom_engine(self) -> None:
        source = (HERE / "strategy.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        class_names = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        }
        self.assertIn("Candidate35Strategy", class_names)
        self.assertNotIn("BacktestEngine", class_names)
        self.assertIn("self.order_factory.bracket", source)
        self.assertIn("self.submit_order_list", source)
        self.assertIn("self.portfolio.is_flat", source)

    def test_one_universe_router_call(self) -> None:
        source = (HERE / "strategy.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("winner, decisions = route_universe("), 1)
        self.assertIn("max_open_positions_observed", source)
        self.assertIn("global_position_violations", source)

    def test_launcher_preloads_candidate35_strategy(self) -> None:
        source = (HERE / "launch.py").read_text(encoding="utf-8")
        self.assertIn('importlib.import_module("strategy")', source)
        self.assertIn('importlib.import_module("strategy_v2")', source)
        self.assertIn('HERE / "strategy.py"', source)
        self.assertIn('HERE / "strategy_v2.py"', source)
        self.assertIn('spec_from_file_location("candidate35_direct_runner"', source)
        self.assertIn("klines.csv.gz", source)

    def test_continuous_runner_installs_same_v2_policy(self) -> None:
        source = (HERE / "run_continuous.py").read_text(encoding="utf-8")
        strategy_import = source.index('importlib.import_module("strategy")')
        policy_import = source.index('importlib.import_module("strategy_v2")')
        runner_load = source.index('spec_from_file_location("candidate35_direct_runner"')
        self.assertLess(strategy_import, policy_import)
        self.assertLess(policy_import, runner_load)
        self.assertIn('HERE / "strategy_v2.py"', source)

    def test_seven_day_workflow_tracks_and_asserts_v2_policy(self) -> None:
        workflow = (
            REPO / ".github" / "workflows" / "candidate-35-seven-day-execution.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('research/candidate-35/strategy_v2.py', workflow)
        self.assertIn('research/candidate-35/router_v2.py', workflow)
        self.assertIn("policy_version'] == 'candidate-35b'", workflow)


if __name__ == "__main__":
    unittest.main()
