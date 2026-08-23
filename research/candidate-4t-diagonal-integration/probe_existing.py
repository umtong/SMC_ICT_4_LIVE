#!/usr/bin/env python3
"""Record the executable API of the existing diagonal/channel candidate.

This is deliberately mechanical: Candidate 4t must reuse the implementation that is
already in the repository before adding anything. The output is a compact API/schema
map consumed by the integration adapter and committed with its source SHA.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "research" / "candidate-diagonal-channel-auction-v1"
WORKFLOW = ROOT / ".github" / "workflows" / "candidate-diagonal-channel-auction-v1.yml"


def annotation(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def literal_strings(tree: ast.AST) -> list[str]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if 1 <= len(text) <= 100 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]*", text):
                values.add(text)
    return sorted(values)


def scan_python(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    functions: list[dict[str, Any]] = []
    classes: list[dict[str, Any]] = []
    imports: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = []
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                args.append({"name": arg.arg, "annotation": annotation(arg.annotation)})
            functions.append({
                "name": node.name,
                "args": args,
                "returns": annotation(node.returns),
                "line": node.lineno,
            })
        elif isinstance(node, ast.ClassDef):
            fields: list[dict[str, Any]] = []
            methods: list[str] = []
            for child in node.body:
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    fields.append({
                        "name": child.target.id,
                        "annotation": annotation(child.annotation),
                    })
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append(child.name)
            classes.append({"name": node.name, "fields": fields, "methods": methods, "line": node.lineno})
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    return {
        "path": str(path.relative_to(ROOT)),
        "functions": functions,
        "classes": classes,
        "imports": sorted(set(imports)),
        "candidate_column_literals": literal_strings(tree),
        "has_main_guard": "if __name__" in text and "__main__" in text,
    }


def help_output(path: Path) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            [sys.executable, str(path), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=25,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
        }
    except Exception as exc:
        return {"error": repr(exc)}


def main() -> None:
    if not TARGET.exists():
        raise FileNotFoundError(TARGET)
    python_files = sorted(TARGET.glob("*.py"))
    report: dict[str, Any] = {
        "target": str(TARGET.relative_to(ROOT)),
        "files": [str(path.relative_to(ROOT)) for path in sorted(TARGET.iterdir())],
        "python": [scan_python(path) for path in python_files],
        "workflow": WORKFLOW.read_text(encoding="utf-8") if WORKFLOW.exists() else None,
        "help": {},
    }
    for path in python_files:
        scanned = next(item for item in report["python"] if item["path"] == str(path.relative_to(ROOT)))
        if scanned["has_main_guard"]:
            report["help"][str(path.relative_to(ROOT))] = help_output(path)
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "diagonal_api_map.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
