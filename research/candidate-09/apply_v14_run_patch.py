#!/usr/bin/env python3
"""Apply the frozen v14 evaluation-contract changes to candidate-09/run.py.

This script intentionally performs exact, assertion-checked replacements. It contains no
strategy search and changes no market signal, execution, cost, or sizing code.
"""

from __future__ import annotations

import re
from pathlib import Path


path = Path(__file__).resolve().parent / "run.py"
text = path.read_text(encoding="utf-8")

text = re.sub(
    r'\A#!/usr/bin/env python3\n"""Run candidate-09 with NautilusTrader and emit reproducible evidence\.\n\n.*?\n"""',
    '''#!/usr/bin/env python3
"""Run candidate-09 with NautilusTrader and emit reproducible evidence.

No search or parameter optimizer is present. The structurally frozen v14 baseline promotes
the v13 boundary-invalidation control to the main scenario and runs three exact causal
ablations on the same predeclared BTC weeks. Screening is pooled across the declared
evaluation period: positive, negative and inactive weeks are all permitted. The existing
three-year BTC evaluation is allowed only after pooled growth, pooled trade count, active-week
coverage and pooled profit-concentration checks pass.
"""''',
    text,
    count=1,
    flags=re.DOTALL,
)

text, count = re.subn(
    r'ABLATIONS = \(\n(?:    "[^"]+",\n)+\)',
    '''ABLATIONS = (
    "baseline",
    "accepted-extreme-stop",
    "salvage-only",
    "no-flow",
)''',
    text,
    count=1,
)
if count != 1:
    raise SystemExit(f"expected one ablation tuple, changed {count}")

start = text.index("def pooled_metrics(")
end = text.index("\ndef diagnose_failure(", start)
replacement = '''def pooled_metrics(
    outcomes: Sequence[RunOutcome],
    trades: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if not outcomes:
        raise ValueError("cannot pool no outcomes")
    nav_multiple = math.prod(1.0 + outcome.total_return for outcome in outcomes)
    total_days = sum(outcome.calendar_days for outcome in outcomes)
    geo = nav_multiple ** (1.0 / total_days) - 1.0 if nav_multiple > 0.0 else -1.0
    total_trades = sum(outcome.trades for outcome in outcomes)
    active_segments = sum(outcome.trades > 0 for outcome in outcomes)
    if trades is None:
        largest_profit_share = max((outcome.largest_profit_share or 0.0) for outcome in outcomes)
    else:
        positive_pnls = [float(row["net_pnl"]) for row in trades if float(row["net_pnl"]) > 0.0]
        positive_total = sum(positive_pnls)
        largest_profit_share = max(positive_pnls, default=0.0) / positive_total if positive_total > 0.0 else 0.0
    return {
        "nav_multiple": nav_multiple,
        "calendar_days": total_days,
        "daily_geometric_return": geo,
        "trades": total_trades,
        "trades_per_day": total_trades / total_days,
        "active_segments": active_segments,
        "all_segments_positive": all(outcome.total_return > 0.0 for outcome in outcomes),
        "maximum_segment_drawdown": max(outcome.max_drawdown for outcome in outcomes),
        "maximum_single_trade_profit_share": largest_profit_share,
        "implementation_ok": all(outcome.implementation_status == "OK" for outcome in outcomes),
    }


def evaluate_gate(config: Mapping[str, Any], baseline: Sequence[DetailedRun]) -> tuple[bool, dict[str, Any]]:
    outcomes = [detail.outcome for detail in baseline]
    trades = [row for detail in baseline for row in detail.trades]
    pooled = pooled_metrics(outcomes, trades)
    gate = config["gate"]
    checks = {
        "implementation_ok": pooled["implementation_ok"],
        "pooled_daily_geometric_return": pooled["daily_geometric_return"]
        >= float(gate["minimum_pooled_daily_geometric_return"]),
        "minimum_total_trades": pooled["trades"] >= int(gate["minimum_total_trades"]),
        "minimum_active_weeks": pooled["active_segments"] >= int(gate["minimum_active_weeks"]),
        "profit_not_single_trade_dominated": pooled["maximum_single_trade_profit_share"]
        <= float(gate["maximum_single_trade_profit_share"]),
    }
    return all(checks.values()), {"pooled": pooled, "checks": checks}


def active_trade_months(trades: Sequence[Mapping[str, Any]]) -> int:
    months: set[tuple[int, int]] = set()
    for row in trades:
        ts_ns = row.get("opened_ns") or row.get("signal_observed_ns")
        if ts_ns is None:
            continue
        stamp = datetime.fromtimestamp(int(ts_ns) / 1_000_000_000, tz=timezone.utc)
        months.add((stamp.year, stamp.month))
    return len(months)


def evaluate_long(config: Mapping[str, Any], detail: DetailedRun) -> tuple[bool, dict[str, Any]]:
    spec = config["long_evaluation"]
    outcome = detail.outcome
    months = active_trade_months(detail.trades)
    minimum_trades = math.ceil(outcome.calendar_days * float(spec["minimum_trades_per_calendar_day"]))
    checks = {
        "implementation_ok": outcome.implementation_status == "OK",
        "daily_geometric_return": outcome.daily_geometric_return
        >= float(spec["success_daily_geometric_return"]),
        "minimum_total_trades": outcome.trades >= minimum_trades,
        "minimum_active_months": months >= int(spec["minimum_active_months"]),
        "profit_not_single_trade_dominated": outcome.largest_profit_share is not None
        and outcome.largest_profit_share <= float(config["gate"]["maximum_single_trade_profit_share"]),
        "recoverable_drawdown": outcome.max_drawdown <= float(spec["maximum_drawdown"]),
    }
    return all(checks.values()), {
        "checks": checks,
        "minimum_total_trades_required": minimum_trades,
        "active_months": months,
        "outcome": asdict(outcome),
    }
'''
text = text[:start] + replacement + text[end:]

text = text.replace(
    "gate_passed, gate_detail = evaluate_gate(config, baseline_outcomes)",
    "gate_passed, gate_detail = evaluate_gate(config, baseline_details)",
    1,
)
text = text.replace(
    '''variant: pooled_metrics([detail.outcome for detail in details])
                for variant, details in by_variant.items()''',
    '''variant: pooled_metrics(
                    [detail.outcome for detail in details],
                    [row for detail in details for row in detail.trades],
                )
                for variant, details in by_variant.items()''',
    1,
)

old = '''        pass_long = (
            detail.outcome.implementation_status == "OK"
            and detail.outcome.daily_geometric_return >= float(spec["success_daily_geometric_return"])
            and detail.outcome.largest_profit_share is not None
            and detail.outcome.largest_profit_share <= float(config["gate"]["maximum_single_trade_profit_share"])
        )
        summary = {
            "candidate": config["candidate"],
            "status": "SUCCESS" if pass_long else "FAILED_LONG_EVALUATION",
            "long_evaluation": {"status": "PASS" if pass_long else "FAIL", "outcome": asdict(detail.outcome)},
        }
'''
new = '''        pass_long, long_detail = evaluate_long(config, detail)
        summary = {
            "candidate": config["candidate"],
            "status": "SUCCESS" if pass_long else "FAILED_LONG_EVALUATION",
            "long_evaluation": {"status": "PASS" if pass_long else "FAIL", **long_detail},
        }
'''
if old not in text:
    raise SystemExit("direct long-evaluation block not found")
text = text.replace(old, new, 1)

old = '''            pass_long = (
                long_detail.outcome.implementation_status == "OK"
                and long_detail.outcome.daily_geometric_return >= float(spec["success_daily_geometric_return"])
                and long_detail.outcome.largest_profit_share is not None
                and long_detail.outcome.largest_profit_share <= float(config["gate"]["maximum_single_trade_profit_share"])
            )
            summary["long_evaluation"] = {
                "status": "PASS" if pass_long else "FAIL",
                "outcome": asdict(long_detail.outcome),
            }
'''
new = '''            pass_long, evaluated_long = evaluate_long(config, long_detail)
            summary["long_evaluation"] = {
                "status": "PASS" if pass_long else "FAIL",
                **evaluated_long,
            }
'''
if old not in text:
    raise SystemExit("gated long-evaluation block not found")
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
