#!/usr/bin/env python3
"""Generate an import-safe active shim for one frozen legacy strategy module."""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def choose_module(parent: Path, patterns: list[str]) -> tuple[Path, str]:
    modules: list[Path] = []
    for pattern in patterns:
        modules.extend(sorted(parent.glob(pattern)))
        if modules:
            break
    if not modules:
        raise RuntimeError(f"no module matched {patterns}")
    for path in modules:
        tree = ast.parse(path.read_text())
        classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
        aliases: list[str] = []
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "LiquidityResponseStrategy"
                for target in node.targets
            ):
                continue
            if isinstance(node.value, ast.Name):
                aliases.append(node.value.id)
        if aliases and aliases[-1] in classes:
            return path, aliases[-1]
    if len(modules) != 1:
        raise RuntimeError(f"cannot unambiguously choose module from {modules}")
    selected = modules[0]
    tree = ast.parse(selected.read_text())
    candidates = [
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.endswith("Strategy")
    ]
    if not candidates:
        raise RuntimeError(f"no Strategy class in {selected}")
    return selected, candidates[-1]


def write_strategy(
    *,
    parent: Path,
    active: Path,
    selected: Path,
    exported: str,
    private_name: str,
) -> None:
    module_name = selected.stem
    active.mkdir(parents=True, exist_ok=True)
    content = f'''"""Generated import-safe shim for frozen {module_name}."""\nfrom __future__ import annotations\nimport importlib.util\nfrom pathlib import Path\nimport sys\n_PARENT = Path(__file__).resolve().parents[1]\n_BASE_PATH = _PARENT / "strategy.py"\n_SPEC = importlib.util.spec_from_file_location("{private_name}", _BASE_PATH)\nif _SPEC is None or _SPEC.loader is None:\n    raise RuntimeError(f"cannot load base strategy from {{_BASE_PATH}}")\n_BASE = importlib.util.module_from_spec(_SPEC)\nsys.modules[_SPEC.name] = _BASE\n_SPEC.loader.exec_module(_BASE)\n_WRAPPER = sys.modules[__name__]\nsys.modules["strategy"] = _BASE\ntry:\n    from {module_name} import {exported} as _Candidate\nfinally:\n    sys.modules["strategy"] = _WRAPPER\nLiquidityResponseConfig = _BASE.LiquidityResponseConfig\nLiquidityResponseStrategy = _Candidate\n__all__ = ["LiquidityResponseConfig", "LiquidityResponseStrategy"]\n'''
    (active / "strategy.py").write_text(content)


def write_candidate(*, active: Path, private_name: str) -> None:
    content = f'''#!/usr/bin/env python3\nfrom __future__ import annotations\nimport importlib.util\nfrom pathlib import Path\nimport subprocess\nimport sys\nfrom typing import Any\nHERE = Path(__file__).resolve().parent\nPARENT = HERE.parent\nif str(PARENT) not in sys.path:\n    sys.path.append(str(PARENT))\nSPEC = importlib.util.spec_from_file_location("{private_name}_pipeline", PARENT / "candidate.py")\nif SPEC is None or SPEC.loader is None:\n    raise RuntimeError("cannot load parent candidate pipeline")\nBASE = importlib.util.module_from_spec(SPEC)\nsys.modules[SPEC.name] = BASE\nSPEC.loader.exec_module(BASE)\ndef isolated(*, config_path: Path, build_start: Any, build_end: Any, evaluation_start: Any, evaluation_end: Any, cache: Path, output: Path):\n    command = [sys.executable, str(Path(__file__).resolve()), "stage", "--config", str(config_path.resolve()), "--build-start", str(build_start), "--build-end", str(build_end), "--evaluation-start", str(evaluation_start), "--evaluation-end", str(evaluation_end), "--cache", str(cache.resolve()), "--output", str(output.resolve())]\n    subprocess.run(command, check=True)\n    return BASE.json.loads((output.resolve() / "metrics.json").read_text())\nBASE.run_backtest_isolated = isolated\nif __name__ == "__main__":\n    BASE.main()\n'''
    (active / "candidate.py").write_text(content)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, default=Path("research/candidate-05"))
    parser.add_argument("--pattern", action="append", required=True)
    parser.add_argument("--active-dir", type=Path, required=True)
    parser.add_argument("--private-name", required=True)
    parser.add_argument("--candidate", action="store_true")
    args = parser.parse_args()
    selected, exported = choose_module(args.parent, args.pattern)
    write_strategy(
        parent=args.parent,
        active=args.active_dir,
        selected=selected,
        exported=exported,
        private_name=args.private_name,
    )
    if args.candidate:
        write_candidate(active=args.active_dir, private_name=args.private_name)
    print(json.dumps({
        "selected": str(selected),
        "module": selected.stem,
        "class": exported,
        "active_dir": str(args.active_dir),
        "candidate": args.candidate,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
