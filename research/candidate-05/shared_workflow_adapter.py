#!/usr/bin/env python3
"""Run an existing shared-account Nautilus workflow command for frozen periods.

This is not a backtest engine.  It extracts the already committed v36 shared
runner command and changes only evaluation dates and output/cache locations.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import textwrap


ORIGINAL_DATES = {
    "2023-09-06": "warmup",
    "2023-09-08": "start",
    "2023-09-14": "end",
}


def _run_blocks(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        match = re.match(r"^(\s*)run:\s*\|\s*$", lines[index])
        if match is None:
            index += 1
            continue
        indent = len(match.group(1))
        index += 1
        captured: list[str] = []
        while index < len(lines):
            line = lines[index]
            stripped = line.lstrip(" ")
            current_indent = len(line) - len(stripped)
            if stripped and current_indent <= indent:
                break
            captured.append(line[indent + 2 :] if len(line) >= indent + 2 else "")
            index += 1
        block = "\n".join(captured)
        if "shared_account_backtest" in block:
            blocks.append(textwrap.dedent(block))
    return blocks


def _replace_period(block: str, *, warmup: str, start: str, end: str, root: Path, label: str) -> str:
    replacements = {
        "2023-09-06": warmup,
        "2023-09-08": start,
        "2023-09-14": end,
    }
    for old, new in replacements.items():
        block = block.replace(old, new)
    block = re.sub(
        r"artifacts/candidate-05-v36[^\s\"']*",
        str(root / label),
        block,
    )
    block = re.sub(
        r"\.cache/candidate-05-v36[^\s\"']*",
        str(Path(".cache") / f"candidate-05-v47-{label}"),
        block,
    )
    return block


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    blocks = _run_blocks(args.workflow)
    if not blocks:
        raise RuntimeError("no existing shared_account_backtest run block found")
    # Prefer the block that actually invokes the runner most often; this keeps
    # its original baseline/candidate integrity comparison intact.
    block = max(blocks, key=lambda value: value.count("shared_account_backtest"))
    periods = (
        ("week-1", "2023-07-07", "2023-07-09", "2023-07-15"),
        ("week-2", "2024-01-13", "2024-01-15", "2024-01-21"),
        ("week-3", "2023-09-06", "2023-09-08", "2023-09-14"),
    )
    args.output_root.mkdir(parents=True, exist_ok=True)
    for label, warmup, start, end in periods:
        script = _replace_period(
            block,
            warmup=warmup,
            start=start,
            end=end,
            root=args.output_root,
            label=label,
        )
        script_path = args.output_root / f"{label}-command.sh"
        script_path.write_text("set -euo pipefail\n" + script + "\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "ROOT": str(args.output_root / label),
                "CACHE": str(Path(".cache") / f"candidate-05-v47-{label}"),
                "PYTHONPATH": "research/candidate-05",
            },
        )
        subprocess.run(["bash", str(script_path)], check=True, env=environment)


if __name__ == "__main__":
    main()
