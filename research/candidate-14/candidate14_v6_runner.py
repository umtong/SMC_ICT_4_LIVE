#!/usr/bin/env python3
"""Reproducible Candidate 14 V6 mechanism-development runner."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_audit import audit
from run_v6_global_initiative import run

V6_FILES = (
    "global_initiative_continuation.py",
    "v6_market_leadership.py",
    "v6_portfolio_materializer.py",
    "run_v6_global_initiative.py",
    "candidate14_v6_runner.py",
    "v6_development_protocol.json",
    "logic.py",
    "market_leadership.py",
    "semantic_market_leadership.py",
    "semantic_logic.py",
    "run_leadership_scdam_base.py",
    "runner_materializer.py",
    "bar_adapter.py",
    "global_allocator.py",
    "session_engine.py",
    "base_config.json",
)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def execute(week: str, output_dir: Path) -> dict[str, Any]:
    protocol = load_object(ROOT / "v6_development_protocol.json")
    weeks = protocol["selection"]["weeks"]
    if week not in weeks:
        raise ValueError(f"unknown V6 development interval {week!r}")

    config = load_object(ROOT / "base_config.json")
    config["candidate"] = protocol["candidate"]
    config["selection"]["warmup_days"] = protocol["selection"]["warmup_days"]
    config["selection"]["evaluation_days"] = protocol["selection"]["evaluation_days"]
    config["selection"]["weeks"] = {
        name: {
            "start": record["start"],
            "end_exclusive": record["end_exclusive"],
        }
        for name, record in weeks.items()
    }
    config["candidate14_v6_protocol"] = {
        "schema": protocol["schema"],
        "validation_mode": protocol["validation_mode"],
        "interval": week,
        "role": weeks[week]["role"],
        "screen": protocol["screen"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    effective = output_dir / "effective_config.json"
    write_json(effective, config)
    write_json(
        output_dir / "source_lock.json",
        {
            "schema": "candidate-14-v6-development-source-lock-v1",
            "candidate": protocol["candidate"],
            "files": {
                name: {
                    "bytes": (ROOT / name).stat().st_size,
                    "sha256": sha256((ROOT / name).read_bytes()).hexdigest(),
                }
                for name in V6_FILES
            },
        },
    )

    metrics = run(effective, week, output_dir)
    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = load_object(metrics_path)
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "candidate14_v6_protocol": protocol["schema"],
            "validation_mode": protocol["validation_mode"],
            "development_role": weeks[week]["role"],
            "success_claim": False,
        }
    )
    write_json(metrics_path, metrics)

    audit_result = audit(output_dir, week)
    write_json(
        output_dir / "audit.json",
        {
            **audit_result,
            "candidate": protocol["candidate"],
            "candidate14_v6_protocol": protocol["schema"],
            "validation_mode": protocol["validation_mode"],
        },
    )
    summary = {
        "candidate": protocol["candidate"],
        "week": week,
        "start": weeks[week]["start"],
        "end_exclusive": weeks[week]["end_exclusive"],
        "role": weeks[week]["role"],
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "closed_trades": metrics.get("closed_trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "final_nav": metrics.get("final_nav"),
        "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
        "scenario_counts": metrics.get("scenario_counts", {}),
        "module_counts": metrics.get("module_counts", {}),
        "skip_reasons": metrics.get("skip_reasons", {}),
        "engine_errors": metrics.get("engine_errors", []),
        "audit_classification": audit_result.get("classification"),
        "implementation_evidence_passed": all(
            audit_result.get(key) is True
            for key in (
                "evidence_complete",
                "metric_recalculation_passed",
                "risk_budget_passed",
                "global_slot_passed",
                "partial_entry_protection_passed",
                "no_liquidation_passed",
                "engine_errors_absent",
            )
        ),
        "success_claim": False,
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if audit_result["classification"] == "IMPLEMENTATION_OR_EVIDENCE_FAILURE":
        raise SystemExit(2)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("week")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    execute(args.week, args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
