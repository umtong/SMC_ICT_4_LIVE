#!/usr/bin/env python3
"""Instantiate the trusted single-asset Nautilus venue configuration exactly.

The four-instrument runner must not approximate fill, latency, fee, margin or
bar-execution settings.  This module reads the already validated
``nt_backtest.py`` AST, identifies the function containing its
``BacktestVenueConfig`` constructor, evaluates only the local assignments on
which that constructor depends, and then evaluates the exact constructor call.

No market data, order matching, fill, position, PnL or NAV logic is implemented
here.  Unsupported dependencies fail closed rather than silently changing the
execution model.
"""
from __future__ import annotations

import ast
import builtins
from functools import lru_cache
from pathlib import Path
from typing import Any

import nt_backtest as trusted


TARGET = "BacktestVenueConfig"
FORBIDDEN_CALLS = {
    "parse_args",
    "parse_known_args",
    "download_checked",
    "load_week",
    "prepare_catalog",
    "BacktestNode",
    "run",
}


def call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    if isinstance(node, ast.AnnAssign):
        target = node.target
    elif len(node.targets) == 1:
        target = node.targets[0]
    else:
        return None
    return target.id if isinstance(target, ast.Name) else None


def free_names(node: ast.AST) -> set[str]:
    return {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }


def calls(node: ast.AST) -> set[str]:
    return {
        name
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        if (name := call_name(item)) is not None
    }


def assignments(scope: ast.FunctionDef) -> dict[str, ast.stmt]:
    result: dict[str, ast.stmt] = {}
    for node in ast.walk(scope):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        name = assigned_name(node)
        if name is not None:
            result[name] = node
    return result


def venue_call(scope: ast.FunctionDef) -> ast.Call | None:
    matches = [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and call_name(node) == TARGET
    ]
    if not matches:
        return None
    canonical = ast.dump(matches[0], include_attributes=False)
    for match in matches[1:]:
        if ast.dump(match, include_attributes=False) != canonical:
            raise RuntimeError(
                f"trusted scope {scope.name!r} has multiple distinct venue calls"
            )
    return matches[0]


@lru_cache(maxsize=1)
def trusted_source_contract() -> tuple[str, tuple[str, ...]]:
    path = Path(trusted.__file__).resolve()
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    scopes = [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ]
    scopes.sort(key=lambda item: (item.name != "main", item.lineno))
    chosen: ast.FunctionDef | None = None
    target: ast.Call | None = None
    for scope in scopes:
        match = venue_call(scope)
        if match is not None:
            chosen = scope
            target = match
            break
    if chosen is None or target is None:
        raise RuntimeError("trusted runner has no BacktestVenueConfig constructor")

    local_assignments = assignments(chosen)
    global_names = set(trusted.__dict__) | set(dir(builtins)) | {"config"}
    ordered: list[ast.stmt] = []
    completed: set[str] = set()
    visiting: set[str] = set()

    def require(name: str) -> None:
        if name in completed or name in global_names:
            return
        node = local_assignments.get(name)
        if node is None:
            raise RuntimeError(
                f"trusted venue depends on unsupported local {name!r} "
                f"inside {chosen.name}()"
            )
        if name in visiting:
            raise RuntimeError(f"cyclic trusted venue dependency {name!r}")
        forbidden = calls(node) & FORBIDDEN_CALLS
        if forbidden:
            raise RuntimeError(
                f"trusted venue dependency {name!r} would execute forbidden "
                f"calls: {sorted(forbidden)}"
            )
        visiting.add(name)
        for child in sorted(free_names(node)):
            require(child)
        ordered.append(node)
        visiting.remove(name)
        completed.add(name)

    for name in sorted(free_names(target)):
        require(name)

    module = ast.Module(body=[*ordered], type_ignores=[])
    ast.fix_missing_locations(module)
    expression = ast.Expression(body=target)
    ast.fix_missing_locations(expression)
    dependency_source = tuple(ast.unparse(node) for node in ordered)
    call_source = ast.unparse(target)
    # Cache source, not code objects, to keep the returned value serializable and
    # directly auditable in evidence.
    return call_source, dependency_source


def make_trusted_venue_config(config: dict[str, Any]) -> Any:
    call_source, dependency_source = trusted_source_contract()
    namespace: dict[str, Any] = {"config": config}
    global_namespace = dict(trusted.__dict__)
    global_namespace["__builtins__"] = builtins.__dict__
    for source in dependency_source:
        tree = ast.parse(source, mode="exec")
        exec(compile(tree, trusted.__file__, "exec"), global_namespace, namespace)
    expression = ast.parse(call_source, mode="eval")
    return eval(
        compile(expression, trusted.__file__, "eval"),
        global_namespace,
        namespace,
    )


def execution_contract_evidence() -> dict[str, Any]:
    call_source, dependencies = trusted_source_contract()
    return {
        "trusted_source": str(Path(trusted.__file__).resolve()),
        "venue_constructor_ast": call_source,
        "dependency_assignments_ast": list(dependencies),
        "market_or_performance_data_accessed": False,
        "execution_model_modified": False,
    }


__all__ = [
    "execution_contract_evidence",
    "make_trusted_venue_config",
    "trusted_source_contract",
]
