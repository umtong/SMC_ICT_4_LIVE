#!/usr/bin/env python3
"""Recover a frozen shared-account command and change only orchestration fields."""
from __future__ import annotations

import argparse
from pathlib import Path
import re


def extract_command(workflow: Path) -> str:
    lines = workflow.read_text().splitlines()
    command_lines: list[str] = []
    collecting = False
    indent = 0
    for line in lines:
        stripped = line.lstrip()
        if (
            not collecting
            and stripped.startswith("python ")
            and "shared_account" in stripped
        ):
            collecting = True
            indent = len(line) - len(stripped)
        if not collecting:
            continue
        current_indent = len(line) - len(stripped)
        if command_lines and line.strip() and current_indent < indent:
            break
        if not line.strip():
            break
        command_lines.append(stripped)
        if not line.rstrip().endswith("\\"):
            break
    if not command_lines:
        raise RuntimeError(f"no shared-account command found in {workflow}")
    return "\n".join(command_lines)


def replace_option(command: str, name: str, value: str) -> str:
    pattern = rf"{re.escape(name)}(?:=|\s+)(?:\"[^\"]*\"|'[^']*'|\S+)"
    replacement = f'{name} "{value}"'
    updated, count = re.subn(pattern, replacement, command, count=1)
    if count != 1:
        raise RuntimeError(f"expected one {name} in frozen command")
    return updated


def adapt(
    *,
    command: str,
    runner: str,
    output: str,
    cache: str,
    build_start: str,
    build_end: str,
    evaluation_start: str,
    evaluation_end: str,
) -> str:
    command, count = re.subn(
        r"python\s+\S*shared_account\S*\.py",
        f"python {runner}",
        command,
        count=1,
    )
    if count != 1:
        raise RuntimeError("could not replace shared-account runner")
    values = {
        "--output": output,
        "--cache": cache,
        "--build-start": build_start,
        "--build-end": build_end,
        "--evaluation-start": evaluation_start,
        "--evaluation-end": evaluation_end,
    }
    for name, value in values.items():
        command = replace_option(command, name, value)
    return command


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--runner", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--build-start", required=True)
    parser.add_argument("--build-end", required=True)
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--evaluation-end", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    command = adapt(
        command=extract_command(args.workflow),
        runner=args.runner,
        output=args.output,
        cache=args.cache,
        build_start=args.build_start,
        build_end=args.build_end,
        evaluation_start=args.evaluation_start,
        evaluation_end=args.evaluation_end,
    )
    args.destination.write_text("set -euo pipefail\n" + command + "\n")
    print(command)


if __name__ == "__main__":
    main()
