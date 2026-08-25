#!/usr/bin/env python3
"""Reproducible entry point for ML-k structural-destination research."""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
from typing import Any

import structural_destination_exec as executable
import structural_destination_policy as policy
import evidence_supported_destination_router as destination_router

RUNNER_VERSION = "candidate-ml-k-structural-destination-reproduction-2"
CONTAINER_IMAGE = (
    "ghcr.io/umtong/smc-ict-4-live-research@"
    "sha256:8f4de8a2b2fa28c3f424d114969b1c07765206708f24613b86896ced67532469"
)
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_DIR = Path(__file__).resolve().parent


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity() -> dict[str, Any]:
    return {
        "source_sha": _git("rev-parse", "HEAD"),
        "source_branch": _git("branch", "--show-current"),
        "runner_version": RUNNER_VERSION,
        "container_image": CONTAINER_IMAGE,
        "python": sys.version,
        "policy_version": policy.POLICY_VERSION,
    }


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )


def verify(output: Path) -> dict[str, Any]:
    compiled: list[str] = []
    for path in sorted(CANDIDATE_DIR.glob("*.py")):
        py_compile.compile(str(path), doraise=True)
        compiled.append(path.name)
    required = {
        "structural_destination_policy.py": "generate_symbol",
        "structural_destination_exec.py": "run_research",
        "evidence_supported_destination_router.py": "route_research",
    }
    modules: dict[str, Any] = {}
    for filename, callable_name in required.items():
        path = CANDIDATE_DIR / filename
        if not path.exists():
            raise FileNotFoundError(path)
        module = __import__(path.stem)
        candidate = getattr(module, callable_name, None)
        if not callable(candidate):
            raise RuntimeError(f"{path.stem}.{callable_name} is not callable")
        modules[path.stem] = {
            "callable": callable_name,
            "sha256": _sha256(path),
        }
    payload = {
        **_identity(),
        "compiled": compiled,
        "modules": modules,
        "fixed_contract": {
            "symbols": list(SYMBOLS),
            "risk_fraction": 0.03,
            "gross_planned_rr_floor": 1.0,
            "one_global_pending_or_position_slot": True,
            "partial_entries_or_exits": False,
        },
    }
    _write(output / "verify.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def harvest(args: argparse.Namespace) -> dict[str, Any]:
    symbols = tuple(args.symbols)
    unknown = sorted(set(symbols) - set(SYMBOLS))
    if unknown:
        raise ValueError(f"unsupported symbols: {unknown}")
    if args.end <= args.start:
        raise ValueError("end must be later than start")
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    summary = dict(
        executable.run_research(
            start=args.start,
            end=args.end,
            warmup_days=args.warmup_days,
            symbols=symbols,
            cache=args.cache,
            output=args.output,
        )
    )
    summary.update(
        {
            "period": args.period,
            "role": args.period.split("-", 1)[0],
            "start": args.start.isoformat(),
            "end": args.end.isoformat(),
            "requested_symbols": list(symbols),
            "decision_window_is_half_open": "[start,end)",
            "source_identity": _identity(),
        }
    )
    _write(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def route(args: argparse.Namespace) -> dict[str, Any]:
    summary = destination_router.route_research(args.root, args.output)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--output", type=Path, required=True)

    harvest_parser = commands.add_parser("harvest")
    harvest_parser.add_argument("--start", type=date.fromisoformat, required=True)
    harvest_parser.add_argument("--end", type=date.fromisoformat, required=True)
    harvest_parser.add_argument("--warmup-days", type=int, default=45)
    harvest_parser.add_argument("--period", required=True)
    harvest_parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    harvest_parser.add_argument("--cache", type=Path, required=True)
    harvest_parser.add_argument("--output", type=Path, required=True)

    route_parser = commands.add_parser("route")
    route_parser.add_argument("--root", type=Path, required=True)
    route_parser.add_argument("--output", type=Path, required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "verify":
        verify(args.output)
    elif args.command == "harvest":
        harvest(args)
    elif args.command == "route":
        route(args)
    else:  # pragma: no cover
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()
