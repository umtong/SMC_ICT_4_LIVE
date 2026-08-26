#!/usr/bin/env python3
"""Apply the inherited exact-target patch, then parameterize only its changed source."""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--fraction", required=True, type=float)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0.15 <= args.fraction <= 0.75:
        raise ValueError("fraction must be between 0.15 and 0.75")
    root = args.root.resolve()
    py_files = sorted(root.rglob("*.py"))
    before = {path: digest(path) for path in py_files}

    inherited_patch = (
        Path(__file__).resolve().parents[1]
        / "candidate-ml-first-liquidity-response-v2"
        / "patch_exact_half_route.py"
    )
    subprocess.run(
        [sys.executable, str(inherited_patch), "--root", str(root)],
        check=True,
    )

    after_half = {path: digest(path) for path in py_files}
    changed = [path for path in py_files if before[path] != after_half[path]]
    if not changed:
        raise RuntimeError("Inherited half-route patch changed no Python source")

    replacement = f"{args.fraction:.8f}".rstrip("0").rstrip(".")
    replacements = 0
    for path in changed:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        rewritten: list[str] = []
        for line in lines:
            lower = line.lower()
            if any(token in lower for token in ("target", "route", "frontier", "objective")):
                new_line, count = re.subn(r"(?<![\w.])0\.50?(?![\w.])", replacement, line)
                replacements += count
                rewritten.append(new_line)
            else:
                rewritten.append(line)
        path.write_text("".join(rewritten), encoding="utf-8")

    if abs(args.fraction - 0.5) > 1e-12 and replacements == 0:
        raise RuntimeError(
            "Located the inherited patch output but could not identify its 0.5 target constant"
        )

    for path in changed:
        subprocess.run([sys.executable, "-m", "py_compile", str(path)], check=True)
    print(
        f"patched {len(changed)} inherited source file(s) to route fraction "
        f"{args.fraction:.4f}; replacements={replacements}"
    )


if __name__ == "__main__":
    main()
