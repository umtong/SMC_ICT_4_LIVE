#!/usr/bin/env python3
"""Freeze one final candidate after unanimous short/holdout/long gates."""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil

from variant_registry_v4 import VARIANTS


CONTRACT = {
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
    "single_global_pending_or_position": True,
    "risk_fraction_current_nav": 0.03,
    "minimum_gross_rr": 1.0,
    "partial_entries": False,
    "partial_exits": False,
    "fixed_holding_exit": False,
    "fee_profile": "usd_m_vip0",
    "entry_slippage_ticks": 2,
    "stop_slippage_ticks": 2,
    "evaluation_engine": "NautilusTrader",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit-sha", required=True)
    return parser.parse_args()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    args = parse_args()
    gates: dict[str, tuple[Path, dict]] = {}
    for path in sorted(args.root.rglob("gate.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        mode = str(report.get("mode") or "")
        if mode in {"short", "holdout", "long"}:
            gates[mode] = (path, report)

    args.output.mkdir(parents=True, exist_ok=True)
    reports = args.output / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    status = {
        mode: {
            "pass": bool(report.get("pass")),
            "selected_variant": report.get("selected_variant"),
            "source": str(path),
            "sha256": digest(path),
        }
        for mode, (path, report) in gates.items()
    }
    (reports / "latest_gate_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for mode, (path, _) in gates.items():
        shutil.copy2(path, reports / f"latest_{mode}_gate.json")
        markdown = path.with_name("gate.md")
        if markdown.exists():
            shutil.copy2(markdown, reports / f"latest_{mode}_gate.md")

    required = {"short", "holdout", "long"}
    if not required <= gates.keys() or not all(bool(gates[mode][1].get("pass")) for mode in required):
        (args.output / "NOT_PROMOTED").write_text(
            "Short, untouched holdout and long continuous gates did not all pass.\n",
            encoding="utf-8",
        )
        return
    selected = {
        str(gates[mode][1].get("selected_variant") or "").strip()
        for mode in required
    }
    if len(selected) != 1 or "" in selected:
        raise RuntimeError(f"gates disagreed on candidate: {selected}")
    variant = selected.pop()
    try:
        spec = VARIANTS[variant]
    except KeyError as exc:
        raise RuntimeError(f"passing gate selected unregistered variant {variant!r}") from exc

    long_path, long_report = gates["long"]
    manifest = {
        "candidate": "candidate-easychart_re1",
        "variant": variant,
        "bundle_import": spec.bundle,
        "venue_strategy_import": spec.venue_strategy,
        "paper_strategy_import": spec.paper_strategy,
        "families": spec.families,
        "mechanisms": spec.mechanisms,
        "frozen_commit_sha": args.commit_sha,
        "promoted_at_utc": datetime.now(UTC).isoformat(),
        "promotion_basis": {
            "short_gate": status["short"],
            "holdout_gate": status["holdout"],
            "long_gate": status["long"],
            "long_gate_sha256": digest(long_path),
            "long_gate_report": long_report,
        },
        "contract": CONTRACT,
        "research_backtest_runner": (
            "research/candidate-easychart_re1/run_mtf_backtest_re1_variant_v11.py"
        ),
        "venue_parity_backtest_runner": (
            "research/candidate-easychart_re1/run_mtf_backtest_re1_venue_frozen_v4.py"
        ),
        "paper_runner": (
            "research/candidate-easychart_re1/run_binance_demo_re1_frozen_v5.py"
        ),
        "paper_environment": "BINANCE_DEMO_USDT_FUTURES",
        "venue_safe_stop_replacement": True,
        "restart_policy": "FAIL_CLOSED_CANCEL_FLATTEN_REQUIRE_CLEAN_RESTART",
    }
    (args.output / "canonical_candidate.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output / "CANONICAL_READY").write_text(
        f"{variant}\n{args.commit_sha}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
