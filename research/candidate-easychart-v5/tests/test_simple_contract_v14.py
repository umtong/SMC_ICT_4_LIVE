from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
import unittest

from simple_contract_v14 import (
    DAILY_LOSS_LIMIT,
    FIXED_RISK_FRACTION,
    MINIMUM_GROSS_RR,
    PARTIAL_PROFIT_TAKING,
    PARTIAL_STOPPING,
    TIME_BASED_FORCED_EXIT,
    TRADE_COUNT_LIMIT,
    contract_record,
)


CANDIDATE_ROOT = Path(__file__).resolve().parents[1]
RESEARCH_ROOT = Path(__file__).resolve().parents[2]
RUNNER = CANDIDATE_ROOT / "run_mtf_backtest_v14_simple.py"
BASE_STRATEGY = RESEARCH_ROOT / "candidate-easychart-v3" / "mtf_strategy.py"
BASE_ORDERS = RESEARCH_ROOT / "candidate-easychart-v3" / "strategy_orders.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class_method(path: Path, class_name: str, method_name: str) -> ast.FunctionDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(f"{class_name}.{method_name} not found in {path}")


def _attribute_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


class SimpleTradingContractTests(unittest.TestCase):
    def test_non_negotiable_values(self) -> None:
        self.assertEqual(FIXED_RISK_FRACTION, Decimal("0.03"))
        self.assertEqual(MINIMUM_GROSS_RR, Decimal("1.0"))
        self.assertFalse(PARTIAL_PROFIT_TAKING)
        self.assertFalse(PARTIAL_STOPPING)
        self.assertIsNone(DAILY_LOSS_LIMIT)
        self.assertIsNone(TIME_BASED_FORCED_EXIT)
        self.assertIsNone(TRADE_COUNT_LIMIT)

    def test_run_record_states_the_same_contract(self) -> None:
        record = contract_record()
        self.assertEqual(record["fixed_risk_fraction"], 0.03)
        self.assertEqual(record["minimum_gross_rr"], 1.0)
        self.assertEqual(
            record["position_management"],
            "ONE_ENTRY_ONE_FULL_STOP_ONE_FULL_TARGET",
        )
        self.assertTrue(record["entry_stop_target_fixed_before_submission"])
        self.assertTrue(record["evaluation_end_flatten_only"])

    def test_base_config_defaults_remain_three_percent_and_one_r(self) -> None:
        tree = _tree(BASE_STRATEGY)
        config = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "EasyChartMTFConfig"
        )
        defaults: dict[str, object] = {}
        for node in config.body:
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            if isinstance(node.value, ast.Constant):
                defaults[node.target.id] = node.value.value
        self.assertEqual(defaults["risk_fraction"], 0.03)
        self.assertEqual(defaults["min_gross_rr"], 1.0)

    def test_canonical_runner_explicitly_uses_fixed_contract(self) -> None:
        source = RUNNER.read_text(encoding="utf-8")
        self.assertIn("risk_fraction=float(FIXED_RISK_FRACTION)", source)
        self.assertIn("min_gross_rr=float(MINIMUM_GROSS_RR)", source)
        self.assertNotIn("--risk-fraction", source)
        self.assertNotIn("--min-gross-rr", source)

    def test_canonical_runner_does_not_import_prohibited_policy_layers(self) -> None:
        imported: set[str] = set()
        for node in _tree(RUNNER).body:
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
        prohibited = {
            "mtf_strategy_day_v7",
            "mtf_strategy_daily_risk_v11",
            "mtf_strategy_exit_v9",
            "partial_management_v12",
            "daytrade_lifecycle_v7",
        }
        self.assertTrue(prohibited.isdisjoint(imported), sorted(imported & prohibited))

    def test_submission_is_one_predeclared_full_bracket(self) -> None:
        method = _class_method(BASE_ORDERS, "EasyChartOrderMixin", "_submit_plan")
        bracket_calls = [
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "bracket"
        ]
        self.assertEqual(len(bracket_calls), 1)
        keywords = {item.arg: item.value for item in bracket_calls[0].keywords if item.arg}
        self.assertIsInstance(keywords["quantity"], ast.Name)
        self.assertEqual(keywords["quantity"].id, "quantity")
        self.assertEqual(_attribute_name(keywords["sl_trigger_price"].args[0]), "plan.stop")
        self.assertEqual(_attribute_name(keywords["tp_price"].args[0]), "plan.target")

    def test_fill_handler_neither_scales_out_nor_moves_protection(self) -> None:
        methods = [
            _class_method(BASE_ORDERS, "EasyChartOrderMixin", "_submit_plan"),
            _class_method(BASE_ORDERS, "EasyChartOrderMixin", "on_order_filled"),
        ]
        called_attributes = {
            node.func.attr
            for method in methods
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        prohibited = {
            "modify_order",
            "close_position",
            "close_all_positions",
            "set_time_alert",
            "set_timer",
        }
        self.assertTrue(prohibited.isdisjoint(called_attributes), sorted(called_attributes))

    def test_strategy_has_no_holding_deadline_or_daily_governor(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (RUNNER, BASE_STRATEGY, BASE_ORDERS)
        ).lower()
        prohibited_tokens = (
            "daily_loss_cap",
            "daily_risk",
            "holding_deadline",
            "max_holding",
            "24_hour_exit",
            "24h_exit",
            "trade_count_limit",
        )
        for token in prohibited_tokens:
            self.assertNotIn(token, sources)


if __name__ == "__main__":
    unittest.main()
