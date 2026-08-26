#!/usr/bin/env python3
"""Parameterize the exact target patch, including materialized/new source files."""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict[Path, str]:
    return {path: digest(path) for path in root.rglob("*.py") if path.is_file()}


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
    before = snapshot(root)
    inherited_patch = (
        Path(__file__).resolve().parents[1]
        / "candidate-ml-first-liquidity-response-v2"
        / "patch_exact_half_route.py"
    )
    subprocess.run(
        [sys.executable, str(inherited_patch), "--root", str(root)],
        check=True,
    )
    after = snapshot(root)
    changed = sorted(path for path, value in after.items() if before.get(path) != value)
    if not changed:
        raise RuntimeError("Inherited exact-target patch changed or created no Python source")

    replacement = f"{args.fraction:.8f}".rstrip("0").rstrip(".")
    replacements = 0
    candidates: list[str] = []
    for path in changed:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        rewritten: list[str] = []
        for number, line in enumerate(lines, start=1):
            lower = line.lower()
            has_half = re.search(r"(?<![\w.])0\.50?(?![\w.])", line) is not None
            if has_half:
                candidates.append(f"{path}:{number}:{line.strip()}")
            if has_half and any(
                token in lower
                for token in (
                    "target",
                    "route",
                    "frontier",
                    "objective",
                    "opposing",
                    "liquidity",
                )
            ):
                new_line, count = re.subn(
                    r"(?<![\w.])0\.50?(?![\w.])",
                    replacement,
                    line,
                )
                replacements += count
                rewritten.append(new_line)
            else:
                rewritten.append(line)
        path.write_text("".join(rewritten), encoding="utf-8")

    if abs(args.fraction - 0.5) > 1e-12 and replacements == 0:
        raise RuntimeError(
            "No target half constant was parameterized. Candidate half-lines:\n"
            + "\n".join(candidates[:100])
        )
    for path in changed:
        subprocess.run([sys.executable, "-m", "py_compile", str(path)], check=True)
    print(
        f"patched {len(changed)} inherited file(s) to route fraction "
        f"{args.fraction:.4f}; replacements={replacements}"
    )


if __name__ == "__main__":
    main()
