#!/usr/bin/env python3
"""Run the broad causal-flow candidate and build research-only plan labels."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sys
import traceback

from counterfactual_plan_harvest_fixed import HarvestConfig, harvest_counterfactual_plans
import run_mtf_backtest_re1_flow as _flow


def _value(argv: list[str], option: str) -> str:
    try:
        return argv[argv.index(option) + 1]
    except (ValueError, IndexError) as exc:
        raise SystemExit(f"missing {option}") from exc


def _values(argv: list[str], option: str, next_option: str) -> tuple[str, ...]:
    try:
        start = argv.index(option) + 1
    except ValueError as exc:
        raise SystemExit(f"missing {option}") from exc
    end = argv.index(next_option, start)
    return tuple(argv[start:end])


if __name__ == "__main__":
    argv = sys.argv
    start = date.fromisoformat(_value(argv, "--start"))
    end = date.fromisoformat(_value(argv, "--end"))
    warmup_days = int(_value(argv, "--warmup-days"))
    symbols = _values(argv, "--symbols", "--cache")
    cache = Path(_value(argv, "--cache"))
    output = Path(_value(argv, "--output"))
    fee_profile = _value(argv, "--fee-profile")
    entry_slippage_ticks = int(_value(argv, "--entry-slippage-ticks")) if "--entry-slippage-ticks" in argv else 2
    stop_slippage_ticks = int(_value(argv, "--stop-slippage-ticks")) if "--stop-slippage-ticks" in argv else 2

    _flow._runner.main()
    _flow._rewrite_metadata(output)
    config = HarvestConfig(
        start=start,
        end=end,
        load_start=start - timedelta(days=warmup_days),
        symbols=symbols,
        cache=cache,
        output=output,
        fee_profile=fee_profile,
        entry_slippage_ticks=entry_slippage_ticks,
        stop_slippage_ticks=stop_slippage_ticks,
    )
    try:
        summary = harvest_counterfactual_plans(config)
    except Exception:
        output.mkdir(parents=True, exist_ok=True)
        failure = traceback.format_exc()
        (output / "counterfactual_failure.txt").write_text(failure, encoding="utf-8")
        print(failure, file=sys.stderr, flush=True)
        raise

    for name in ("metrics.json", "run.json"):
        path = output / name
        if not path.exists():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        record.update(
            {
                "candidate": "candidate-easychart_re1_counterfactual_harvest",
                "counterfactual_harvest": summary,
                "counterfactual_usage": "RESEARCH_LABELS_ONLY_NOT_AVAILABLE_TO_STRATEGY",
            },
        )
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
