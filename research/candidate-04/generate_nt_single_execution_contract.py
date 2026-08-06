#!/usr/bin/env python3
"""Generate a reusable execution-model factory from ``nt_backtest.py``.

The generator copies the AST expressions that instantiate NautilusTrader's
fee, fill and latency models in the trusted single-asset runner.  It does not
invent, approximate or serialize model settings by hand.  The generated factory
is then imported by the four-instrument runner so both execution paths use the
same models.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
from typing import Iterable


TARGET_CLASSES = {
    "ImportableFeeModelConfig": "fee_model",
    "ImportableFillModelConfig": "fill_model",
    "ImportableLatencyModelConfig": "latency_model",
}
MARKER_START = "# BEGIN GENERATED SINGLE-ASSET EXECUTION CONTRACT"
MARKER_END = "# END GENERATED SINGLE-ASSET EXECUTION CONTRACT"


def call_name(call: ast.Call) -> str | None:
    function = call.func
    if isinstance(function, ast.Name):
        return function.id
    if isinstance(function, ast.Attribute):
        return function.attr
    return None


def assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    target = node.target if isinstance(node, ast.AnnAssign) else (
        node.targets[0] if len(node.targets) == 1 else None
    )
    return target.id if isinstance(target, ast.Name) else None


def all_assignments(function: ast.FunctionDef) -> dict[str, ast.stmt]:
    result: dict[str, ast.stmt] = {}
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            name = assigned_name(node)
            if name:
                result[name] = node
    return result


def free_names(node: ast.AST) -> set[str]:
    names = {
        item.id
        for item in ast.walk(node)
        if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load)
    }
    return names


def dependencies(
    node: ast.AST,
    assignments: dict[str, ast.stmt],
    globals_: set[str],
) -> list[ast.stmt]:
    ordered: list[ast.stmt] = []
    visiting: set[str] = set()
    completed: set[str] = set()

    def visit_name(name: str) -> None:
        if name in completed or name in globals_ or name in {"config"}:
            return
        dependency = assignments.get(name)
        if dependency is None:
            raise RuntimeError(
                f"execution model expression depends on unsupported local {name!r}"
            )
        if name in visiting:
            raise RuntimeError(f"cyclic local dependency for {name!r}")
        visiting.add(name)
        value = dependency.value
        for child in sorted(free_names(value)):
            visit_name(child)
        ordered.append(dependency)
        visiting.remove(name)
        completed.add(name)

    for name in sorted(free_names(node)):
        visit_name(name)
    return ordered


def find_main(tree: ast.Module) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise RuntimeError("nt_backtest.py has no main()")


def model_assignments(
    function: ast.FunctionDef,
) -> dict[str, tuple[str, ast.Assign | ast.AnnAssign]]:
    result: dict[str, tuple[str, ast.Assign | ast.AnnAssign]] = {}
    for node in ast.walk(function):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        class_name = call_name(value)
        if class_name not in TARGET_CLASSES:
            continue
        variable = assigned_name(node) or TARGET_CLASSES[class_name]
        result[class_name] = (variable, node)
    missing = sorted(set(TARGET_CLASSES) - set(result))
    if missing:
        raise RuntimeError(
            "trusted single-asset runner does not instantiate: "
            + ", ".join(missing)
        )
    return result


def unparse_statements(statements: Iterable[ast.stmt], indent: str) -> list[str]:
    lines: list[str] = []
    for statement in statements:
        source = ast.unparse(statement)
        lines.extend(indent + line for line in source.splitlines())
    return lines


def generate(source_path: Path, output_path: Path) -> str:
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    main = find_main(tree)
    assignments = all_assignments(main)
    models = model_assignments(main)
    global_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            global_names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            global_names.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            name = assigned_name(node)
            if name:
                global_names.add(name)
    global_names.update(dir(__builtins__))

    dependency_nodes: list[ast.stmt] = []
    seen_source: set[str] = set()
    for _, node in models.values():
        for dependency in dependencies(node.value, assignments, global_names):
            rendered = ast.unparse(dependency)
            if rendered not in seen_source:
                dependency_nodes.append(dependency)
                seen_source.add(rendered)

    lines = [
        '#!/usr/bin/env python3',
        '"""Generated exact Nautilus execution model factory.',
        '',
        f'Source: {source_path.name}.  Regenerate with',
        '``generate_nt_single_execution_contract.py`` after any trusted runner change.',
        '"""',
        'from __future__ import annotations',
        '',
        'from typing import Any',
        '',
        'from nautilus_trader.backtest.config import ImportableFeeModelConfig',
        'from nautilus_trader.backtest.config import ImportableFillModelConfig',
        'from nautilus_trader.backtest.config import ImportableLatencyModelConfig',
        '',
        MARKER_START,
        '',
        'def make_execution_model_configs(',
        '    config: dict[str, Any],',
        ') -> dict[str, Any]:',
    ]
    if dependency_nodes:
        lines.extend(unparse_statements(dependency_nodes, "    "))
    returned: list[str] = []
    for class_name in TARGET_CLASSES:
        variable, node = models[class_name]
        standard = TARGET_CLASSES[class_name]
        call_source = ast.unparse(node.value)
        lines.append(f"    {standard} = {call_source}")
        returned.append(standard)
    lines.extend(
        [
            '    return {',
            *[f'        "{name}": {name},' for name in returned],
            '    }',
            '',
            MARKER_END,
            '',
            '__all__ = ["make_execution_model_configs"]',
            '',
        ]
    )
    generated = "\n".join(lines)
    ast.parse(generated, filename=str(output_path))
    output_path.write_text(generated, encoding="utf-8")
    return generated


def patch_multi_runner(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    import_line = (
        "from nt_single_execution_contract import "
        "make_execution_model_configs\n"
    )
    if import_line not in text:
        marker = "import nt_backtest as single_base\n"
        if marker not in text:
            raise RuntimeError("multi runner import marker not found")
        text = text.replace(marker, marker + import_line, 1)

    old_signature = '''def build_run_config(
    catalog_path: Path,
    strategies: list[ImportableStrategyConfig],
    evaluation_start: date,
    evaluation_end: date,
    starting_nav: float,
) -> BacktestRunConfig:
'''
    new_signature = '''def build_run_config(
    catalog_path: Path,
    strategies: list[ImportableStrategyConfig],
    evaluation_start: date,
    evaluation_end: date,
    starting_nav: float,
    execution_models: dict[str, Any],
) -> BacktestRunConfig:
'''
    if old_signature in text:
        text = text.replace(old_signature, new_signature, 1)
    elif new_signature not in text:
        raise RuntimeError("build_run_config signature marker not found")

    old_venue_end = '''        "use_reduce_only": True,
    }
    venue = BacktestVenueConfig(
'''
    new_venue_end = '''        "use_reduce_only": True,
    }
    for model_name in ("fee_model", "fill_model", "latency_model"):
        model = execution_models.get(model_name)
        if model is not None:
            venue_values[model_name] = model
    venue = BacktestVenueConfig(
'''
    if old_venue_end in text:
        text = text.replace(old_venue_end, new_venue_end, 1)
    elif new_venue_end not in text:
        raise RuntimeError("venue model insertion marker not found")

    old_call = '''    run_config = build_run_config(
        catalog_path,
        strategies,
        args.evaluation_start,
        args.evaluation_end,
        float(config["starting_nav"]),
    )
'''
    new_call = '''    execution_models = make_execution_model_configs(config)
    run_config = build_run_config(
        catalog_path,
        strategies,
        args.evaluation_start,
        args.evaluation_end,
        float(config["starting_nav"]),
        execution_models,
    )
'''
    if old_call in text:
        text = text.replace(old_call, new_call, 1)
    elif new_call not in text:
        raise RuntimeError("build_run_config call marker not found")
    ast.parse(text, filename=str(path))
    path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--multi-runner", type=Path, required=True)
    args = parser.parse_args()
    generate(args.source, args.output)
    patch_multi_runner(args.multi_runner)


if __name__ == "__main__":
    main()
