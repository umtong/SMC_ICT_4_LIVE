#!/usr/bin/env python3
"""Map executable scenario-family code already present in the repository."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def ann(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:
        return None


def scan(directory: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        functions: list[dict[str, Any]] = []
        classes: list[dict[str, Any]] = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append({
                    "name": node.name,
                    "args": [arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]],
                    "returns": ann(node.returns),
                    "line": node.lineno,
                })
            elif isinstance(node, ast.ClassDef):
                fields = [
                    child.target.id
                    for child in node.body
                    if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
                ]
                classes.append({"name": node.name, "fields": fields, "line": node.lineno})
        files.append({
            "path": str(path.relative_to(ROOT)),
            "functions": functions,
            "classes": classes,
        })
    readme = directory / "README.md"
    return {
        "directory": str(directory.relative_to(ROOT)),
        "readme": readme.read_text(encoding="utf-8") if readme.exists() else None,
        "files": files,
    }


def main() -> None:
    output = Path(sys.argv[1])
    names = sys.argv[2:]
    report = {}
    for name in names:
        directory = ROOT / "research" / name
        report[name] = scan(directory) if directory.exists() else {"missing": True}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
