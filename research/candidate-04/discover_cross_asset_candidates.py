#!/usr/bin/env python3
"""Discover frozen BTC-long winners from workflow artifacts.

The utility reads completed evidence only.  It neither opens market data nor
recalculates performance.  Successful BTC-long candidates are converted into
explicit compiler/route/source-commit specifications for integrated validation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any, Iterable


COMPILERS = {
    "v33": "rich_signal_compiler_v31.py",
    "v34": "post_event_inventory_resolution_compiler.py",
    "v35": "event_time_flow_run_compiler.py",
    "v36": "micro_auction_balance_transition_compiler.py",
}


def load(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def family_of(value: dict[str, Any]) -> str | None:
    explicit = str(value.get("family") or "").lower()
    if explicit in COMPILERS:
        return explicit
    text = " ".join(
        str(value.get(key) or "")
        for key in (
            "candidate",
            "compiler",
            "workflow",
            "stage",
        )
    ).lower()
    for family in COMPILERS:
        if family in text:
            return family
    return None


def source_commit_of(value: dict[str, Any], fallback: str) -> str:
    for item in walk(value):
        for key in ("source_commit", "source_checkout_ref", "head_sha"):
            candidate = item.get(key)
            if isinstance(candidate, str) and re.fullmatch(
                r"[0-9a-fA-F]{40}", candidate
            ):
                return candidate.lower()
    return fallback.lower()


def routes_of(value: dict[str, Any], family: str) -> list[str]:
    successful = value.get("successful_routes")
    if isinstance(successful, list) and successful:
        return sorted({str(item) for item in successful})
    route = value.get("route") or value.get("survivor")
    if isinstance(route, str) and route in {"full", "continuation", "reversal"}:
        return [route]
    candidate = str(value.get("candidate") or "").lower()
    if "continuation" in candidate:
        return ["continuation"]
    if "reversal" in candidate:
        return ["reversal"]
    if family == "v36" and isinstance(value.get("routes"), dict):
        routes = [
            name
            for name, item in value["routes"].items()
            if isinstance(item, dict)
            and (
                item.get("candidate_pass")
                or item.get("project_target_reached")
            )
        ]
        if routes:
            return sorted(set(routes))
    return ["full"]


def compiler_of(value: dict[str, Any], family: str) -> str:
    explicit = value.get("compiler")
    if isinstance(explicit, str) and explicit.endswith(".py"):
        return Path(explicit).name
    for item in walk(value):
        explicit = item.get("compiler")
        if isinstance(explicit, str) and explicit.endswith(".py"):
            return Path(explicit).name
    return COMPILERS[family]


def reached(value: dict[str, Any]) -> bool:
    return any(
        bool(item.get("project_target_reached"))
        for item in walk(value)
        if "project_target_reached" in item
    )


def final_evidence(root: Path) -> list[tuple[Path, dict[str, Any]]]:
    records: list[tuple[Path, dict[str, Any]]] = []
    if not root.exists():
        return records
    for path in sorted(root.rglob("*.json")):
        value = load(path)
        if not isinstance(value, dict):
            continue
        if path.name == "final_decision.json" or (
            value.get("final_validation_completed") is True
            and "project_target_reached" in value
        ):
            records.append((path, value))
    return records


def discover(
    root: Path,
    fallback_head_sha: str,
    origin_workflow: str,
    origin_run_id: int,
) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path, value in final_evidence(root):
        if not reached(value):
            continue
        family = family_of(value)
        if family is None:
            continue
        source_commit = source_commit_of(value, fallback_head_sha)
        compiler = compiler_of(value, family)
        for route in routes_of(value, family):
            if route not in {"full", "continuation", "reversal"}:
                continue
            key = (family, route, source_commit)
            candidate_id = f"{family}-{route}-{source_commit[:10]}"
            candidates[key] = {
                "candidate_id": candidate_id,
                "family": family,
                "route": route,
                "compiler": compiler,
                "source_commit": source_commit,
                "origin_workflow": origin_workflow,
                "origin_run_id": origin_run_id,
                "origin_evidence": str(path.relative_to(root)),
                "source_paths": [
                    f"research/candidate-04/{compiler}",
                    "research/candidate-04/nt_exact_causal_target_risk_sizing.py",
                    "research/candidate-04/nt_backtest_v31_exact_causal_target.py",
                    "research/candidate-04/nt_rich_signal_strategy.py",
                    "research/candidate-04/inventory_transfer_config.json",
                    "research/candidate-04/impact_exhaustion_config.json",
                    "research/candidate-04/auction_activity_router_config.json",
                    "research/candidate-04/nt_liquidity_config.json",
                ],
            }
    return [candidates[key] for key in sorted(candidates)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", args.head_sha):
        raise SystemExit("head SHA must be a 40-character Git commit")
    candidates = discover(
        args.artifacts,
        args.head_sha,
        args.workflow,
        args.run_id,
    )
    result = {
        "origin_workflow": args.workflow,
        "origin_run_id": args.run_id,
        "origin_head_sha": args.head_sha.lower(),
        "successful_btc_long_candidates": candidates,
        "candidate_count": len(candidates),
        "performance_recalculated": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
