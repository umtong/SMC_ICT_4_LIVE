#!/usr/bin/env python3
"""Candidate 13 V11 exposed-development Nautilus runner."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence_audit import audit
from logic import BarObs, LogicConfig, MINUTE_NS
from quarter_hour_failed_initiative import (
    QHF_MODULE,
    QuarterHourFailedInitiativeEngine,
    SYMBOLS,
)
from quarter_hour_failed_materializer import materialize_failed_initiative_source

SAFETY_KEYS = (
    "evidence_complete",
    "metric_recalculation_passed",
    "risk_budget_passed",
    "global_slot_passed",
    "partial_entry_protection_passed",
    "no_liquidation_passed",
    "engine_errors_absent",
)


def build_run() -> Any:
    import market_leadership as _market_leadership
    from runner_materializer import materialize_runner_source
    from semantic_logic import install as _install_semantic_logic
    from semantic_market_leadership import SemanticMarketLeadershipGate
    from semantic_post_gate import amend_after_leadership

    _market_leadership.MarketLeadershipGate = SemanticMarketLeadershipGate
    _install_semantic_logic()
    base = ROOT / "run_leadership_scdam_base.py"
    source = materialize_runner_source(base.read_text(encoding="utf-8"))
    source = materialize_failed_initiative_source(source)
    namespace = {
        "__name__": "candidate13_v11_failed_qh_materialized",
        "__file__": str(base),
        "amend_after_leadership": amend_after_leadership,
    }
    exec(compile(source, str(base), "exec"), namespace)
    return namespace["run"]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def execute(interval: str, output_dir: Path) -> dict[str, Any]:
    protocol_path = ROOT / "protocol-v11-quarter-hour-failed-initiative-development.json"
    protocol = load_object(protocol_path)
    holdouts = protocol["selection"]["holdouts"]
    if interval not in holdouts:
        raise ValueError(f"unknown V11 interval {interval!r}; expected {sorted(holdouts)}")

    config = load_object(ROOT / "base_config.json")
    config["candidate"] = protocol["candidate"]
    config["selection"]["seed"] = protocol["selection"]["seed"]
    config["selection"]["warmup_days"] = protocol["selection"]["warmup_days"]
    config["selection"]["evaluation_days"] = protocol["selection"]["evaluation_days"]
    config["selection"]["weeks"] = {
        name: {
            "start": record["start"],
            "end_exclusive": record["end_exclusive"],
        }
        for name, record in holdouts.items()
    }
    config["candidate13_v11"] = {
        "schema": protocol["schema"],
        "interval": interval,
        "role": holdouts[interval]["role"],
        "development_gate": protocol["development_gate"],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    effective = output_dir / "effective_config.json"
    write_json(effective, config)
    lock_files = (
        "quarter_hour_failed_initiative.py",
        "quarter_hour_failed_materializer.py",
        "quarter_hour_persistent_initiative.py",
        "candidate13_v11_runner.py",
        "aggregate_v11_development.py",
        "protocol-v11-quarter-hour-failed-initiative-development.json",
        "run_leadership_scdam_base.py",
        "runner_materializer.py",
        "semantic_logic.py",
        "semantic_market_leadership.py",
        "semantic_post_gate.py",
        "logic.py",
        "market_leadership.py",
        "session_engine.py",
        "bar_adapter.py",
        "global_allocator.py",
        "evidence_audit.py",
        "base_config.json",
    )
    write_json(
        output_dir / "source_lock.json",
        {
            "schema": "candidate-13-v11-development-source-lock-v1",
            "candidate": protocol["candidate"],
            "files": {
                name: {
                    "bytes": (ROOT / name).stat().st_size,
                    "sha256": sha256((ROOT / name).read_bytes()).hexdigest(),
                }
                for name in lock_files
            },
        },
    )

    run = build_run()
    metrics = run(effective, interval, output_dir)
    metrics_path = output_dir / "metrics.json"
    if metrics_path.is_file():
        metrics = load_object(metrics_path)
    metrics.update(
        {
            "candidate": protocol["candidate"],
            "candidate13_v11_protocol": protocol["schema"],
            "development_role": holdouts[interval]["role"],
            "development_only": True,
            "success_claim": False,
        },
    )
    write_json(metrics_path, metrics)

    result = audit(output_dir, interval)
    result.update(
        {
            "candidate": protocol["candidate"],
            "candidate13_v11_protocol": protocol["schema"],
            "development_only": True,
        },
    )
    write_json(output_dir / "audit.json", result)
    summary = {
        "candidate": protocol["candidate"],
        "interval": interval,
        "start": holdouts[interval]["start"],
        "end_exclusive": holdouts[interval]["end_exclusive"],
        "role": holdouts[interval]["role"],
        "daily_geometric_growth": metrics.get("daily_geometric_growth"),
        "closed_trades": metrics.get("closed_trades"),
        "wins": metrics.get("wins"),
        "losses": metrics.get("losses"),
        "win_rate": metrics.get("win_rate"),
        "payoff_ratio": metrics.get("payoff_ratio"),
        "final_nav": metrics.get("final_nav"),
        "closed_trade_max_drawdown": metrics.get("closed_trade_max_drawdown"),
        "submitted_plans": metrics.get("submitted_plans"),
        "scenario_counts": metrics.get("scenario_counts", {}),
        "module_counts": metrics.get("module_counts", {}),
        "symbol_counts": metrics.get("symbol_counts", {}),
        "skip_reasons": metrics.get("skip_reasons", {}),
        "engine_errors": metrics.get("engine_errors", []),
        "audit_classification": result.get("classification"),
        "safety_audit_passed": all(result.get(key) is True for key in SAFETY_KEYS),
        "success_claim": False,
    }
    write_json(output_dir / "summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def self_test() -> None:
    from runner_materializer import materialize_runner_source

    base = (ROOT / "run_leadership_scdam_base.py").read_text(encoding="utf-8")
    materialized = materialize_failed_initiative_source(
        materialize_runner_source(base),
    )
    for token in (
        "QuarterHourFailedInitiativeEngine(\n                logic_config,",
        "].on_batch(ts_ns, self.buffer):",
        "plans.append((failure_plan, failure_candidate))",
        "candidate-13-v11-protective-failure-fail-close",
        "candidate-13-v11-strict-open-time",
    ):
        assert materialized.count(token) == 1, token

    config = LogicConfig()
    engine = QuarterHourFailedInitiativeEngine(config)
    prices = {
        symbol: 100.0 + index * 10.0
        for index, symbol in enumerate(SYMBOLS)
    }
    start = int(
        datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000,
    )
    plans = []
    for minute in range(1, 56):
        batch: dict[str, BarObs] = {}
        first_event = 31 <= minute <= 35
        second_event = 46 <= minute <= 50
        failure = 51 <= minute <= 55
        for index, symbol in enumerate(SYMBOLS):
            accepted = symbol != "XRPUSDT"
            open_price = prices[symbol]
            if first_event and accepted:
                drift = 0.40
                taker = 720.0
            elif second_event and accepted:
                drift = 0.15
                taker = 700.0
            elif failure and accepted:
                drift = -0.18
                taker = 300.0
            else:
                drift = 0.001
                taker = 500.0
            close_price = open_price + drift
            high = max(open_price, close_price) + 0.01
            low = min(open_price, close_price) - 0.01
            ts_ns = start + minute * MINUTE_NS
            batch[symbol] = BarObs(
                ts_ns,
                open_price,
                high,
                low,
                close_price,
                1000.0,
                taker,
            )
            prices[symbol] = close_price
        emitted = engine.on_batch(start + minute * MINUTE_NS, batch)
        if emitted:
            plans.extend(emitted)
    assert plans, engine.skips
    assert all(plan.details["module"] == QHF_MODULE for _, plan in plans)
    assert all(plan.entry_order_type == "LIMIT" for _, plan in plans)
    assert all(plan.entry_post_only for _, plan in plans)
    assert all(plan.net_r >= config.min_net_r for _, plan in plans)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("interval")
    run_parser.add_argument("output_dir", type=Path)
    sub.add_parser("self-test")
    args = parser.parse_args()
    if args.command == "run":
        execute(args.interval, args.output_dir.resolve())
    else:
        self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
