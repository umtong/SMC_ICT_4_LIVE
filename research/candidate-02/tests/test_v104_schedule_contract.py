from __future__ import annotations

import ast
import inspect

import v104_external_liquidity_core as v104


def _is_name(node: ast.AST | None, value: str) -> bool:
    return isinstance(node, ast.Name) and node.id == value


def test_signal_schedule_contract_is_exactly_one_minute_and_causal() -> None:
    config = v104.ExternalLiquidityConfig()
    assert config.activation_delay_minutes == 1

    tree = ast.parse(inspect.getsource(v104.build_scenario_result))
    activation_assignment = False
    rotation_contract = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "activation_ns"
            for target in node.targets
        ):
            expression = node.value
            if (
                isinstance(expression, ast.BinOp)
                and isinstance(expression.op, ast.Add)
                and _is_name(expression.left, "decision_ns")
                and isinstance(expression.right, ast.BinOp)
                and isinstance(expression.right.op, ast.Mult)
                and isinstance(expression.right.left, ast.Attribute)
                and _is_name(expression.right.left.value, "config")
                and expression.right.left.attr == "activation_delay_minutes"
                and _is_name(expression.right.right, "NS_MINUTE")
            ):
                activation_assignment = True
        if isinstance(node, ast.Call):
            function = node.func
            if not (
                _is_name(function, "RotationSignal")
                or isinstance(function, ast.Attribute) and function.attr == "RotationSignal"
            ):
                continue
            keywords = {keyword.arg: keyword.value for keyword in node.keywords if keyword.arg}
            rotation_contract = (
                _is_name(keywords.get("observed_time_ns"), "activation_ns")
                and _is_name(keywords.get("source_feature_available_time_ns"), "activation_ns")
                and _is_name(keywords.get("source_max_market_time_ns"), "decision_ns")
            )
    assert activation_assignment
    assert rotation_contract
