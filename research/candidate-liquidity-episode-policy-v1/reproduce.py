#!/usr/bin/env python3
"""Repository-local reproduction entry point for liquidity-episode-policy-v1.

This file deliberately contains orchestration only. The trading policy remains in
``episode_policy.py`` and the strict chronological account router remains in
``route_episode_policy_causal.py``.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import importlib
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
from typing import Any, Iterable

import pandas as pd


RUNNER_VERSION = "candidate-liquidity-episode-policy-v1-reproduction-1"
CONTAINER_IMAGE = (
    "ghcr.io/umtong/smc-ict-4-live-research@"
    "sha256:8f4de8a2b2fa28c3f424d114969b1c07765206708f24613b86896ced67532469"
)
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")

REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_DIR = Path(__file__).resolve().parent
DEPENDENCY_DIRS = (
    CANDIDATE_DIR,
    REPO_ROOT / "research/candidate-liquidity-world-model-v1",
    REPO_ROOT / "research/candidate-liquidity-auction-v2",
    REPO_ROOT / "research/candidate-liquidity-auction-v7",
    REPO_ROOT / "research/candidate-liquidity-auction-v6",
    REPO_ROOT / "research/candidate-liquidity-auction-v5",
    REPO_ROOT / "research/candidate-coherent-auction-system-v4",
    REPO_ROOT / "research/candidate-coherent-auction-system-v3",
    REPO_ROOT / "research/candidate-coherent-liquidity-policy-v2",
    REPO_ROOT / "research/candidate-coherent-liquidity-policy-v1",
    REPO_ROOT / "research/candidate-hierarchical-liquidity-bpr-v2",
    REPO_ROOT / "research/candidate-hierarchical-liquidity-bpr-v1",
    REPO_ROOT / "research/candidate-liquidity-displacement-v1",
    REPO_ROOT / "research/candidate-auction-dislocation-confluence-v1",
    REPO_ROOT / "research/candidate-derivatives-dislocation-v1",
    REPO_ROOT / "research/candidate-auction-episode-policy",
    REPO_ROOT / "research/candidate-auction-event-v2",
    REPO_ROOT / "research/candidate-direct-auction-policy",
    REPO_ROOT / "research/candidate-easychart_re1",
    REPO_ROOT / "research/candidate-easychart-v5",
    REPO_ROOT / "research/candidate-easychart-v3",
)


def _activate_repo_paths() -> None:
    paths = [str(path) for path in DEPENDENCY_DIRS if path.exists()]
    for path in reversed(paths):
        if path not in sys.path:
            sys.path.insert(0, path)
    inherited = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        paths + ([inherited] if inherited else [])
    )


_activate_repo_paths()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    value = completed.stdout.strip()
    return value or None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
        + "\n",
        encoding="utf-8",
    )


def _source_identity() -> dict[str, Any]:
    return {
        "source_sha": _git("rev-parse", "HEAD"),
        "source_branch": _git("branch", "--show-current"),
        "runner_version": RUNNER_VERSION,
        "container_image": CONTAINER_IMAGE,
        "python": sys.version,
        "symbols": list(SYMBOLS),
    }


def _candidate_source_files() -> list[Path]:
    return sorted(
        path
        for path in CANDIDATE_DIR.iterdir()
        if path.is_file() and path.suffix in {".py", ".txt", ".md", ".json"}
    )


def verify(output: Path) -> dict[str, Any]:
    required = {
        "episode_policy.py": "generate_symbol",
        "episode_policy_exec.py": "main",
        "route_episode_policy.py": "route_account",
        "route_episode_policy_causal.py": "strict_causal_predictions",
        "episode_policy_features.py": "enrich_episode_frame",
    }
    compiled: list[str] = []
    for path in sorted(CANDIDATE_DIR.glob("*.py")):
        py_compile.compile(str(path), doraise=True)
        compiled.append(path.name)

    modules: dict[str, dict[str, Any]] = {}
    for filename, callable_name in required.items():
        path = CANDIDATE_DIR / filename
        if not path.exists():
            raise FileNotFoundError(f"required restored source is missing: {path}")
        module_name = path.stem
        module = importlib.import_module(module_name)
        candidate = getattr(module, callable_name, None)
        if not callable(candidate):
            raise RuntimeError(
                f"{module_name}.{callable_name} is not importable and callable"
            )
        modules[module_name] = {
            "callable": callable_name,
            "path": str(path.relative_to(REPO_ROOT)),
            "sha256": _sha256(path),
        }

    episode_policy = importlib.import_module("episode_policy")
    causal_router = importlib.import_module("route_episode_policy_causal")
    base_router = importlib.import_module("route_episode_policy")
    feature_module = importlib.import_module("episode_policy_features")

    invariants = {
        "one_plan_per_episode_assertion_present": callable(
            getattr(episode_policy, "_assert_policy_invariants", None)
        ),
        "strict_mature_label_router_installed": (
            base_router.causal_predictions
            is causal_router.strict_causal_predictions
        ),
        "strict_pointwise_features_installed": (
            base_router._numeric_features
            is causal_router.causal_numeric_features
        ),
        "risk_fraction": float(base_router.RISK_FRACTION),
        "feature_count": len(feature_module.FEATURE_COLUMNS),
        "allowed_symbols": list(SYMBOLS),
    }
    if invariants["risk_fraction"] != 0.03:
        raise RuntimeError("restored account risk fraction is not 3%")
    if not invariants["strict_mature_label_router_installed"]:
        raise RuntimeError("strict chronological prediction policy was not installed")
    if not invariants["strict_pointwise_features_installed"]:
        raise RuntimeError("pointwise causal feature conversion was not installed")

    payload = {
        **_source_identity(),
        "compiled": compiled,
        "modules": modules,
        "invariants": invariants,
        "restoration_provenance": {
            "base_router_blob_sha1": "92459a08e98a634ec0a096ec1d567c78abdff7a9",
            "base_router_historical_commit": (
                "8ec7bbc6c6f29b0bae5b2d386106056ca8697d4e"
            ),
            "policy_history_tip_before_branch_restoration": (
                "1e87c2d5fce56ef3b9d4bb103951a88311e2a7bc"
            ),
        },
        "candidate_file_sha256": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in _candidate_source_files()
        },
    }
    _write_json(output / "verify.json", payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def harvest(
    *,
    start: date,
    end: date,
    warmup_days: int,
    symbols: Iterable[str],
    cache: Path,
    output: Path,
    period: str,
) -> dict[str, Any]:
    if end <= start:
        raise ValueError("end must be later than start")
    symbols = tuple(symbols)
    unknown = sorted(set(symbols) - set(SYMBOLS))
    if unknown:
        raise ValueError(f"unsupported symbols: {unknown}")

    module = importlib.import_module("episode_policy_exec")
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    summary = module.run_research(
        start=start,
        end=end,
        warmup_days=warmup_days,
        symbols=symbols,
        cache=cache,
        output=output,
    )
    summary = dict(summary)
    summary.update(
        {
            "period": period,
            "role": period.split("-", 1)[0],
            "source_identity": _source_identity(),
            "runner_version": RUNNER_VERSION,
            "container_image": CONTAINER_IMAGE,
            "decision_window_is_half_open": "[start,end)",
            "requested_symbols": list(symbols),
        }
    )
    _write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    return summary


def route(root: Path, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(CANDIDATE_DIR / "route_episode_policy_causal.py"),
        "--root",
        str(root),
        "--output",
        str(output),
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        check=False,
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)


def inspect(root: Path, output: Path) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    action_files: list[dict[str, Any]] = []
    seen_action_ids: set[str] = set()
    duplicate_action_ids: set[str] = set()

    for summary_path in sorted(root.glob("**/summary.json")):
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid JSON: {summary_path}") from exc
        summaries.append(
            {
                "path": str(summary_path.relative_to(root)),
                "sha256": _sha256(summary_path),
                "period": payload.get("period"),
                "start": payload.get("start"),
                "end": payload.get("end"),
                "plans": payload.get("plans"),
                "episodes": payload.get("episodes"),
                "source_sha": (
                    payload.get("source_identity", {}).get("source_sha")
                    if isinstance(payload.get("source_identity"), dict)
                    else payload.get("source_sha")
                ),
            }
        )

    for action_path in sorted(root.glob("**/departure_actions.csv.gz")):
        frame = pd.read_csv(action_path, low_memory=False)
        if "action_id" in frame:
            identifiers = frame["action_id"].dropna().astype(str)
            for identifier in identifiers:
                if identifier in seen_action_ids:
                    duplicate_action_ids.add(identifier)
                seen_action_ids.add(identifier)
        order_exists = (
            frame.get("order_exists", pd.Series(False, index=frame.index))
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes"})
        )
        action_files.append(
            {
                "path": str(action_path.relative_to(root)),
                "sha256": _sha256(action_path),
                "rows": int(len(frame)),
                "orders": int(order_exists.sum()),
                "episodes": (
                    int(frame["episode_id"].nunique())
                    if "episode_id" in frame
                    else None
                ),
                "duplicate_episode_orders": (
                    int(
                        frame.loc[order_exists, "episode_id"]
                        .astype(str)
                        .duplicated(keep=False)
                        .sum()
                    )
                    if "episode_id" in frame
                    else None
                ),
            }
        )

    if not summaries:
        raise RuntimeError(f"no summary.json found under {root}")
    if not action_files:
        raise RuntimeError(f"no departure_actions.csv.gz found under {root}")
    if duplicate_action_ids:
        raise RuntimeError(
            f"duplicate action IDs across artifacts: {sorted(duplicate_action_ids)[:10]}"
        )

    payload = {
        **_source_identity(),
        "root": str(root),
        "summaries": summaries,
        "action_files": action_files,
        "unique_action_ids": len(seen_action_ids),
        "duplicate_action_ids": 0,
        "all_four_symbols_requested": all(
            set(item.get("requested_symbols", [])) == set(SYMBOLS)
            for path in root.glob("**/summary.json")
            for item in [json.loads(path.read_text(encoding="utf-8"))]
            if item.get("requested_symbols") is not None
        ),
    }
    _write_json(output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--output", type=Path, required=True)

    harvest_parser = subparsers.add_parser("harvest")
    harvest_parser.add_argument("--start", type=date.fromisoformat, required=True)
    harvest_parser.add_argument("--end", type=date.fromisoformat, required=True)
    harvest_parser.add_argument("--warmup-days", type=int, default=75)
    harvest_parser.add_argument("--period", required=True)
    harvest_parser.add_argument("--symbols", nargs="+", default=list(SYMBOLS))
    harvest_parser.add_argument("--cache", type=Path, required=True)
    harvest_parser.add_argument("--output", type=Path, required=True)

    route_parser = subparsers.add_parser("route")
    route_parser.add_argument("--root", type=Path, required=True)
    route_parser.add_argument("--output", type=Path, required=True)

    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--root", type=Path, required=True)
    inspect_parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "verify":
        verify(args.output)
    elif args.command == "harvest":
        harvest(
            start=args.start,
            end=args.end,
            warmup_days=args.warmup_days,
            symbols=args.symbols,
            cache=args.cache,
            output=args.output,
            period=args.period,
        )
    elif args.command == "route":
        route(args.root, args.output)
    elif args.command == "inspect":
        inspect(args.root, args.output)
    else:  # pragma: no cover
        raise AssertionError(args.command)


if __name__ == "__main__":
    main()
