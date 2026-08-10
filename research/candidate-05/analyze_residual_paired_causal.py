#!/usr/bin/env python3
"""Causal anatomy for the fixed v52/v53 paired shared-account experiment.

The output is deliberately not a candidate pass/fail gate.  It separates
implementation validity, opportunity reachability, completed causal episodes,
no-trade/event reasons, and the exact mechanism by which v53 differs from v52.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable


FUNNEL_KEYS = (
    "v52_peer_context_ready",
    "v52_extremes",
    "v52_inflections",
    "v52_oi_contraction_pass",
    "v52_flow_depth_pass",
    "v52_setups",
    "v52_same_timestamp_peer_uses",
    "v53_catchup_context",
    "v53_catchup_oi_pass",
    "v53_catchup_flow_pass",
    "v53_catchup_setups",
)


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def finite_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, str):
        try:
            result = float(value.strip().split()[0].replace("_", "").replace(",", ""))
        except (ValueError, IndexError):
            return None
        return result if math.isfinite(result) else None
    return None


def summed_diagnostics(metrics: dict[str, Any], run_dir: Path) -> dict[str, int]:
    values: defaultdict[str, int] = defaultdict(int)
    nested = metrics.get("strategy_diagnostics")
    if isinstance(nested, dict) and nested:
        sources = [item for item in nested.values() if isinstance(item, dict)]
    else:
        sources = []
        for path in run_dir.rglob("strategy_diagnostics.json"):
            value = read_json(path, {})
            if isinstance(value, dict):
                sources.append(value)
    for diagnostic in sources:
        for key, value in diagnostic.items():
            if isinstance(value, bool):
                values[key] += int(value)
            elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                values[key] += int(value)
    return dict(values)


def iter_closed_records(run_dir: Path) -> Iterable[dict[str, Any]]:
    aggregate = run_dir / "closed_scenarios_all.json"
    paths = [aggregate] if aggregate.exists() else list(run_dir.rglob("closed_scenarios.json"))
    seen: set[tuple[Any, ...]] = set()
    for path in paths:
        payload = read_json(path, [])
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict):
                continue
            record = dict(item)
            key = (
                record.get("symbol"),
                record.get("scenario_id"),
                record.get("ts_event") or record.get("closed_ts_event"),
                str(record.get("realized_pnl")),
            )
            if key in seen:
                continue
            seen.add(key)
            yield record


def mechanism_of(record: dict[str, Any]) -> str:
    scenario_id = str(record.get("scenario_id") or "")
    raw = json.dumps(record, sort_keys=True, default=str).upper()
    if scenario_id.startswith("v53-") or "CROSS_SECTIONAL_CATCHUP" in raw:
        return "CROSS_SECTIONAL_CATCHUP"
    if scenario_id.startswith("v52-") or "CROSS_SECTIONAL_RESIDUAL" in raw:
        return "CROSS_SECTIONAL_RESIDUAL_REJECTION"
    return str(record.get("branch") or "UNKNOWN")


def side_of(record: dict[str, Any]) -> int:
    side = finite_number(record.get("side"))
    if side is not None and side != 0.0:
        return 1 if side > 0.0 else -1
    event = str(record.get("event") or "")
    match = re.search(r"entry=(BUY|SELL)", event)
    if match:
        return 1 if match.group(1) == "BUY" else -1
    return 0


def opened_ts_of(record: dict[str, Any]) -> int:
    for key in ("opened_ts_event", "ts_opened", "entry_ts", "episode_ts", "created_ts"):
        value = record.get(key)
        if isinstance(value, int):
            return value
    event = str(record.get("event") or "")
    match = re.search(r"ts_opened=(\d+)", event)
    return int(match.group(1)) if match else 0


def closed_episode_anatomy(run_dir: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    grouped: defaultdict[str, list[float]] = defaultdict(list)
    episodes: list[dict[str, Any]] = []
    for record in iter_closed_records(run_dir):
        pnl = finite_number(record.get("realized_pnl"))
        if pnl is None:
            continue
        mechanism = mechanism_of(record)
        grouped[mechanism].append(pnl)
        episodes.append(
            {
                "symbol": record.get("symbol"),
                "scenario_id": record.get("scenario_id"),
                "mechanism": mechanism,
                "side": side_of(record),
                "opened_ts": opened_ts_of(record),
                "closed_ts": record.get("ts_event") or record.get("closed_ts_event"),
                "realized_pnl": pnl,
                "raw_branch": record.get("branch"),
            },
        )
    anatomy: dict[str, dict[str, Any]] = {}
    for mechanism, values in sorted(grouped.items()):
        gross_profit = sum(value for value in values if value > 0.0)
        gross_loss = -sum(value for value in values if value < 0.0)
        anatomy[mechanism] = {
            "trades": len(values),
            "wins": sum(value > 0.0 for value in values),
            "losses": sum(value < 0.0 for value in values),
            "net_pnl": sum(values),
            "gross_profit": gross_profit,
            "gross_loss": gross_loss,
            "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
            "largest_winner_share": (
                max((value for value in values if value > 0.0), default=0.0) / gross_profit
                if gross_profit > 0.0
                else None
            ),
        }
    episodes.sort(key=lambda item: (int(item.get("opened_ts") or 0), str(item.get("symbol") or "")))
    return anatomy, episodes


def event_anatomy(run_dir: Path) -> dict[str, Any]:
    event_types: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    residual_events = 0
    catchup_events = 0
    unique_scenarios: set[str] = set()
    for path in run_dir.rglob("scenario_events.jsonl"):
        try:
            stream = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                scenario_id = str(event.get("scenario_id") or "")
                raw = json.dumps(event, sort_keys=True, default=str).upper()
                relevant = scenario_id.startswith(("v52-", "v53-")) or "CROSS_SECTIONAL" in raw
                if not relevant:
                    continue
                unique_scenarios.add(scenario_id)
                event_types[str(event.get("event_type") or "UNKNOWN")] += 1
                reasons[str(event.get("reason_code") or "UNKNOWN")] += 1
                residual_events += int(scenario_id.startswith("v52-") or "CROSS_SECTIONAL_RESIDUAL" in raw)
                catchup_events += int(scenario_id.startswith("v53-") or "CROSS_SECTIONAL_CATCHUP" in raw)
    return {
        "unique_residual_scenarios_in_event_log": len(unique_scenarios),
        "residual_event_rows": residual_events,
        "catchup_event_rows": catchup_events,
        "event_type_counts": dict(event_types.most_common()),
        "reason_code_counts": dict(reasons.most_common()),
    }


def reachability_interpretation(funnel: dict[str, int], trades: int) -> str:
    if funnel.get("v52_same_timestamp_peer_uses", 0) > 0:
        return "IMPLEMENTATION_CAUSALITY_VIOLATION_SAME_TIMESTAMP_PEER"
    if funnel.get("v52_peer_context_ready", 0) == 0:
        return "PEER_CONTEXT_OR_SCHEDULING_BLOCKED"
    if funnel.get("v52_extremes", 0) == 0:
        return "NO_ROBUST_RESIDUAL_EXTREMES"
    if funnel.get("v52_inflections", 0) == 0:
        return "EXTREMES_EXIST_BUT_NO_CAUSAL_CONVERGENCE"
    if funnel.get("v52_oi_contraction_pass", 0) == 0 and funnel.get("v53_catchup_oi_pass", 0) == 0:
        return "POSITIONING_STATE_EXPLAINS_REJECTION"
    if funnel.get("v52_flow_depth_pass", 0) == 0 and funnel.get("v53_catchup_flow_pass", 0) == 0:
        return "FLOW_DEPTH_STATE_EXPLAINS_REJECTION"
    if funnel.get("v52_setups", 0) + funnel.get("v53_catchup_setups", 0) > 0 and trades == 0:
        return "SETUPS_EXIST_BUT_CONFIRMATION_GEOMETRY_EXECUTION_OR_SLOT_REJECTS"
    if trades > 0:
        return "ECONOMIC_EPISODES_OBSERVED"
    return "UNRESOLVED_REACHABILITY"


def summarize_run(run_dir: Path) -> dict[str, Any]:
    failure = read_json(run_dir / "failure.json")
    metrics = read_json(run_dir / "metrics.json")
    if failure is not None and metrics is None:
        return {
            "implementation_status": "IMPLEMENTATION_BLOCKED",
            "failure": failure,
            "path": str(run_dir),
        }
    if not isinstance(metrics, dict):
        return {
            "implementation_status": "MISSING_EVIDENCE",
            "path": str(run_dir),
        }
    diagnostics = summed_diagnostics(metrics, run_dir)
    funnel = {key: int(diagnostics.get(key, 0) or 0) for key in FUNNEL_KEYS}
    trades = int(metrics.get("trades", 0) or 0)
    anatomy, episodes = closed_episode_anatomy(run_dir)
    implementation_valid = bool(metrics.get("integrity_pass")) and funnel.get("v52_same_timestamp_peer_uses", 0) == 0
    return {
        "implementation_status": "IMPLEMENTATION_VALID" if implementation_valid else "IMPLEMENTATION_INVALID",
        "path": str(run_dir),
        "evaluation_start": metrics.get("evaluation_start"),
        "evaluation_end": metrics.get("evaluation_end"),
        "calendar_days": metrics.get("calendar_days"),
        "trades": trades,
        "wins": int(metrics.get("wins", 0) or 0),
        "losses": int(metrics.get("losses", 0) or 0),
        "total_return": metrics.get("total_return"),
        "geometric_daily_growth": metrics.get("geometric_daily_growth"),
        "profit_factor": metrics.get("profit_factor"),
        "expectancy_usdt": metrics.get("expectancy_usdt"),
        "gross_profit": metrics.get("gross_profit"),
        "gross_loss": metrics.get("gross_loss"),
        "max_drawdown": metrics.get("max_drawdown"),
        "largest_winner_share": metrics.get("largest_winner_share"),
        "integrity_checks": metrics.get("integrity_checks"),
        "reachability_funnel": funnel,
        "reachability_interpretation": reachability_interpretation(funnel, trades),
        "mechanism_anatomy": anatomy,
        "episodes": episodes,
        "event_anatomy": event_anatomy(run_dir),
    }


def nearest_preservation(v52_episodes: list[dict[str, Any]], v53_episodes: list[dict[str, Any]]) -> dict[str, Any]:
    left = [item for item in v52_episodes if item.get("mechanism") == "CROSS_SECTIONAL_RESIDUAL_REJECTION"]
    right = [item for item in v53_episodes if item.get("mechanism") == "CROSS_SECTIONAL_RESIDUAL_REJECTION"]
    used: set[int] = set()
    matches: list[dict[str, Any]] = []
    tolerance_ns = 10 * 60 * 1_000_000_000
    for source in left:
        candidates: list[tuple[int, int, dict[str, Any]]] = []
        for index, target in enumerate(right):
            if index in used:
                continue
            if source.get("symbol") != target.get("symbol") or source.get("side") != target.get("side"):
                continue
            distance = abs(int(source.get("opened_ts") or 0) - int(target.get("opened_ts") or 0))
            if distance <= tolerance_ns:
                candidates.append((distance, index, target))
        if not candidates:
            continue
        _, index, target = min(candidates, key=lambda item: item[0])
        used.add(index)
        matches.append(
            {
                "symbol": source.get("symbol"),
                "side": source.get("side"),
                "v52_scenario_id": source.get("scenario_id"),
                "v53_scenario_id": target.get("scenario_id"),
                "open_time_distance_seconds": abs(int(source.get("opened_ts") or 0) - int(target.get("opened_ts") or 0)) / 1e9,
                "v52_pnl": source.get("realized_pnl"),
                "v53_pnl": target.get("realized_pnl"),
            },
        )
    return {
        "v52_residual_trades": len(left),
        "v53_residual_trades": len(right),
        "matched_residual_episodes": len(matches),
        "v52_episode_preservation_fraction": len(matches) / len(left) if left else None,
        "matches": matches,
    }


def compare_pair(v52: dict[str, Any], v53: dict[str, Any]) -> dict[str, Any]:
    if v52.get("implementation_status") != "IMPLEMENTATION_VALID" or v53.get("implementation_status") != "IMPLEMENTATION_VALID":
        return {"causal_comparison": "NOT_INTERPRETABLE_UNTIL_IMPLEMENTATION_VALID"}
    v52_return = finite_number(v52.get("total_return")) or 0.0
    v53_return = finite_number(v53.get("total_return")) or 0.0
    catchup = v53.get("mechanism_anatomy", {}).get("CROSS_SECTIONAL_CATCHUP", {})
    residual_52 = v52.get("mechanism_anatomy", {}).get("CROSS_SECTIONAL_RESIDUAL_REJECTION", {})
    residual_53 = v53.get("mechanism_anatomy", {}).get("CROSS_SECTIONAL_RESIDUAL_REJECTION", {})
    catchup_net = finite_number(catchup.get("net_pnl")) or 0.0
    delta_pnl = (finite_number(v53.get("gross_profit")) or 0.0) - (finite_number(v53.get("gross_loss")) or 0.0) - ((finite_number(v52.get("gross_profit")) or 0.0) - (finite_number(v52.get("gross_loss")) or 0.0))
    if int(catchup.get("trades", 0) or 0) == 0:
        attribution = "CATCHUP_STATE_NOT_EXECUTED"
    elif catchup_net <= 0.0:
        attribution = "CATCHUP_EXECUTED_BUT_NEGATIVE"
    elif delta_pnl <= 0.0:
        attribution = "CATCHUP_POSITIVE_BUT_OTHER_V53_CHANGES_OFFSET_IT"
    elif catchup_net >= 0.5 * delta_pnl:
        attribution = "V53_IMPROVEMENT_MATERIALLY_ATTRIBUTABLE_TO_CATCHUP"
    else:
        attribution = "V53_IMPROVEMENT_NOT_CLEANLY_ATTRIBUTABLE_TO_CATCHUP"
    return {
        "causal_comparison": "INTERPRETABLE",
        "total_return_delta_v53_minus_v52": v53_return - v52_return,
        "net_pnl_delta_v53_minus_v52": delta_pnl,
        "trade_delta_v53_minus_v52": int(v53.get("trades", 0)) - int(v52.get("trades", 0)),
        "v52_residual_mechanism": residual_52,
        "v53_residual_mechanism": residual_53,
        "v53_catchup_mechanism": catchup,
        "residual_episode_preservation": nearest_preservation(v52.get("episodes", []), v53.get("episodes", [])),
        "hypothesis_attribution": attribution,
    }


def mechanism_status(report: dict[str, Any]) -> dict[str, str]:
    pairs = list(report["periods"].values())
    if not pairs or any(row[family].get("implementation_status") != "IMPLEMENTATION_VALID" for row in pairs for family in ("v52", "v53")):
        return {"implementation": "BLOCKED_OR_INVALID", "h52": "NOT_INTERPRETABLE", "h53": "NOT_INTERPRETABLE"}
    v52_trades = sum(int(row["v52"].get("trades", 0)) for row in pairs)
    v52_net = sum((finite_number(row["v52"].get("gross_profit")) or 0.0) - (finite_number(row["v52"].get("gross_loss")) or 0.0) for row in pairs)
    catchup_trades = sum(int(row["v53"].get("mechanism_anatomy", {}).get("CROSS_SECTIONAL_CATCHUP", {}).get("trades", 0) or 0) for row in pairs)
    catchup_net = sum(finite_number(row["v53"].get("mechanism_anatomy", {}).get("CROSS_SECTIONAL_CATCHUP", {}).get("net_pnl")) or 0.0 for row in pairs)
    catchup_setups = sum(int(row["v53"].get("reachability_funnel", {}).get("v53_catchup_setups", 0) or 0) for row in pairs)
    if v52_trades == 0:
        h52 = "UNRESOLVED_NO_EXECUTED_RESIDUAL_EPISODES"
    elif v52_net > 0.0:
        h52 = "PROVISIONALLY_SUPPORTED_IN_DIAGNOSTIC_EPISODES"
    else:
        h52 = "REFUTED_OR_MISSING_STATE_DISCRIMINATOR_IN_TESTED_DEFINITION"
    if catchup_setups == 0:
        h53 = "UNRESOLVED_CATCHUP_STATE_NOT_REACHED"
    elif catchup_trades == 0:
        h53 = "UNRESOLVED_CATCHUP_SETUPS_NOT_EXECUTED"
    elif catchup_net > 0.0:
        h53 = "PROVISIONALLY_SUPPORTED_IF_PAIRED_ATTRIBUTION_IS_CLEAN"
    else:
        h53 = "REFUTED_IN_EXECUTED_CATCHUP_EPISODES"
    return {"implementation": "VALID", "h52": h52, "h53": h53}


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# Candidate 05 v52/v53 paired causal anatomy",
        "",
        "This is a mechanism diagnosis, not a binary strategy gate. Separate account returns are not summed into a claimed continuous account result.",
        "",
        f"Mechanism status: `{json.dumps(report['mechanism_status'], sort_keys=True)}`",
        "",
    ]
    for period, payload in report["periods"].items():
        lines.extend([f"## {period}", ""])
        for family in ("v52", "v53"):
            row = payload[family]
            lines.append(
                f"- **{family}** — {row.get('implementation_status')}; trades={row.get('trades', 0)}, "
                f"return={row.get('total_return')}, PF={row.get('profit_factor')}, "
                f"reachability={row.get('reachability_interpretation')}"
            )
            lines.append(f"  - funnel: `{json.dumps(row.get('reachability_funnel', {}), sort_keys=True)}`")
            lines.append(f"  - mechanisms: `{json.dumps(row.get('mechanism_anatomy', {}), sort_keys=True)}`")
        lines.extend(["", f"Paired comparison: `{json.dumps(payload['comparison'], sort_keys=True)}`", ""])
    lines.extend([
        "## Interpretation discipline",
        "",
        "- An implementation-blocked run carries no economic conclusion.",
        "- A higher v53 return does not support the catch-up hypothesis unless catch-up episodes themselves explain the paired improvement.",
        "- Genuine v52 residual episodes should remain preserved unless an explicit mutually exclusive state competes for the same causal event.",
        "- Zero trades are decomposed through the reachability funnel rather than called a strategy failure.",
        "- These inspected diagnostic windows are development data; any supported structure must move to new data without threshold tuning.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    periods: dict[str, Any] = {}
    if args.root.exists():
        for period_dir in sorted(path for path in args.root.iterdir() if path.is_dir() and path.name.startswith("week-")):
            v52 = summarize_run(period_dir / "v52")
            v53 = summarize_run(period_dir / "v53")
            periods[period_dir.name] = {"v52": v52, "v53": v53, "comparison": compare_pair(v52, v53)}
    report: dict[str, Any] = {
        "schema": "candidate-05-v52-v53-paired-causal-anatomy-v1",
        "purpose": "falsify residual convergence and catch-up state hypotheses without random search or binary candidate gating",
        "periods": periods,
    }
    report["mechanism_status"] = mechanism_status(report)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "CAUSAL_ANATOMY.json").write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    write_markdown(args.output / "CAUSAL_ANATOMY.md", report)
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
