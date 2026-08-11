#!/usr/bin/env python3
"""Parity-gated causal lifecycle anatomy for the source-faithful Winner15m account."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
import json
import math
from pathlib import Path
import shutil
from typing import Any

import winner_source_fidelity_campaign as source

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
EVIDENCE = HERE / "evidence" / "winner-lifecycle-forensic-v3"
REFERENCE = HERE / "evidence" / "winner-source-fidelity-v1"
FREEZE = HERE / "WINNER_LIFECYCLE_FORENSIC_V3_FREEZE.md"

PERIODS = {
    "march_2025": {
        "data_start": date(2025, 2, 27),
        "entry_start": date(2025, 3, 3),
        "entry_end": date(2025, 3, 9),
        "data_end": date(2025, 3, 17),
    },
    "september_2024": {
        "data_start": date(2024, 9, 5),
        "entry_start": date(2024, 9, 9),
        "entry_end": date(2024, 9, 15),
        "data_end": date(2024, 9, 24),
    },
}


def finite_number(value: Any, default: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        try:
            result = source.parse_number(value)
        except Exception:
            return default
    return result if math.isfinite(result) else default


def safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe(item) for item in value]
    return value


def dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            safe(payload),
            indent=2,
            sort_keys=True,
            allow_nan=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def configure_period(label: str, spec: dict[str, date]) -> Path:
    work = ROOT / ".work" / "candidate-57-winner-lifecycle-forensic-v3" / label
    output = ROOT / "artifacts" / "candidate-57-winner-lifecycle-forensic-v3" / label
    cache = ROOT / ".cache" / "candidate-57-winner-lifecycle-forensic-v3" / label
    compact = EVIDENCE / "source-accounts" / label
    source.WORK = work
    source.OUTPUT = output
    source.CACHE = cache
    source.EVIDENCE = compact
    source.DATA_START = spec["data_start"]
    source.ENTRY_START = spec["entry_start"]
    source.ENTRY_END = spec["entry_end"]
    source.DATA_END = spec["data_end"]
    return output


def run_period(label: str, spec: dict[str, date]) -> dict[str, Any]:
    output = configure_period(label, spec)
    status = source.main()
    summary_path = source.EVIDENCE / "summary.json"
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.is_file()
        else {"produced": False, "returncode": status}
    )
    return {
        "label": label,
        "status": status,
        "summary": summary,
        "output": output,
        "compact": source.EVIDENCE,
    }


def scenario_trade_key(record: dict[str, Any]) -> tuple[Any, ...]:
    diagnostics = record.get("diagnostics") or {}
    return (
        str(record.get("symbol")),
        int(record.get("side") or 0),
        int(record.get("episode_ts") or 0),
        int(diagnostics.get("causal_episode_start_ts") or 0),
        round(finite_number(record.get("realized_pnl"), 0.0), 8),
    )


def parity_check(row: dict[str, Any]) -> dict[str, Any]:
    reference_summary_path = REFERENCE / "summary.json"
    reference_closed_path = REFERENCE / "closed_scenarios.json"
    candidate_closed_path = row["compact"] / "closed_scenarios.json"
    if not all(
        path.is_file()
        for path in (
            reference_summary_path,
            reference_closed_path,
            candidate_closed_path,
        )
    ):
        return {"pass": False, "reason": "PARITY_INPUT_MISSING"}
    reference_summary = json.loads(reference_summary_path.read_text(encoding="utf-8"))
    reference_closed = json.loads(reference_closed_path.read_text(encoding="utf-8"))
    candidate_closed = json.loads(candidate_closed_path.read_text(encoding="utf-8"))
    reference_keys = sorted(scenario_trade_key(item) for item in reference_closed)
    candidate_keys = sorted(scenario_trade_key(item) for item in candidate_closed)
    summary = row["summary"]
    ending_delta = abs(
        finite_number(summary.get("ending_nav"), math.inf)
        - finite_number(reference_summary.get("ending_nav"), -math.inf)
    )
    expectancy_delta = abs(
        finite_number(summary.get("mean_after_cost_r"), math.inf)
        - finite_number(reference_summary.get("mean_after_cost_r"), -math.inf)
    )
    pf_delta = abs(
        finite_number(summary.get("profit_factor"), math.inf)
        - finite_number(reference_summary.get("profit_factor"), -math.inf)
    )
    passed = bool(
        row["status"] == 0
        and reference_keys == candidate_keys
        and int(summary.get("raw_trades") or -1)
        == int(reference_summary.get("raw_trades") or -2)
        and ending_delta <= 1e-6
        and expectancy_delta <= 1e-12
        and pf_delta <= 1e-12
        and (summary.get("end_state") or {}).get("end_flat")
    )
    return {
        "pass": passed,
        "candidate_status": row["status"],
        "trade_keys_identical": reference_keys == candidate_keys,
        "reference_trades": len(reference_keys),
        "candidate_trades": len(candidate_keys),
        "ending_nav_delta": ending_delta,
        "expectancy_delta": expectancy_delta,
        "profit_factor_delta": pf_delta,
    }


def load_events(path: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not path.is_file():
        return result
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip():
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        scenario_id = str(event.get("scenario_id") or "")
        if scenario_id:
            result[scenario_id].append(event)
    return result


def event_text(event: dict[str, Any]) -> str:
    fields = (
        event.get("event_type"),
        event.get("event"),
        event.get("type"),
        event.get("name"),
        event.get("reason"),
    )
    return " ".join(str(value) for value in fields if value is not None).upper()


def event_ts(event: dict[str, Any]) -> int:
    for key in ("ts_event", "timestamp_ns", "event_ts", "ts_init"):
        try:
            value = int(event.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def actual_r(record: dict[str, Any]) -> float:
    pnl = finite_number(record.get("realized_pnl"), 0.0)
    risk = finite_number(record.get("risk_budget"), math.nan)
    return pnl / risk if math.isfinite(risk) and risk > 0.0 else math.nan


def analyze_period(row: dict[str, Any]) -> dict[str, Any]:
    compact = row["compact"]
    closed_path = compact / "closed_scenarios.json"
    events_path = compact / "scenario_events.jsonl"
    records = (
        json.loads(closed_path.read_text(encoding="utf-8"))
        if closed_path.is_file()
        else []
    )
    events_by_id = load_events(events_path)
    ledger: list[dict[str, Any]] = []
    groups: Counter[str] = Counter()
    component_failures: Counter[str] = Counter()

    for record in records:
        scenario_id = str(record.get("scenario_id") or "")
        events = events_by_id.get(scenario_id, [])
        text = " ".join(event_text(item) for item in events)
        trade_r = actual_r(record)
        trailing_winner = bool(
            "WINNER_TRAILING_EXIT" in text
            and math.isfinite(trade_r)
            and trade_r > 0.0
        )
        hard_stop_like = bool(math.isfinite(trade_r) and trade_r <= -0.80)
        if trailing_winner:
            group = "trailing_winner"
        elif hard_stop_like:
            group = "hard_stop_like_loss"
        elif math.isfinite(trade_r) and trade_r < 0.0:
            group = "other_loss"
        else:
            group = "other_win"
        groups[group] += 1

        snapshots = list(record.get("winner_lifecycle_snapshots") or [])
        trigger_snapshots = [
            item for item in snapshots if int(item.get("direct_thesis_failure") or 0)
        ]
        first_trigger = min(
            trigger_snapshots,
            key=lambda item: int(item.get("ts_event") or 0),
            default=None,
        )
        terminal_ts = max((event_ts(item) for item in events), default=0)
        trigger_ts = int((first_trigger or {}).get("ts_event") or 0)
        trigger_before_terminal = bool(
            first_trigger is not None
            and trigger_ts > 0
            and terminal_ts > 0
            and trigger_ts < terminal_ts
        )
        if first_trigger is not None:
            for component in (
                "ema_supports_entry",
                "macd_supports_entry",
                "roc_supports_entry",
                "adx_supports_entry",
                "volume_supports_entry",
            ):
                if not int(first_trigger.get(component) or 0):
                    component_failures[component] += 1

        ledger.append(
            {
                "scenario_id": scenario_id,
                "symbol": record.get("symbol"),
                "side": record.get("side"),
                "episode_ts": record.get("episode_ts"),
                "actual_r": trade_r,
                "outcome_group": group,
                "snapshot_count": len(snapshots),
                "first_thesis_failure": first_trigger,
                "terminal_event_ts": terminal_ts,
                "trigger_before_terminal": trigger_before_terminal,
                "persistent_source_condition": int(
                    (record.get("diagnostics") or {}).get(
                        "persistent_source_condition",
                        0,
                    )
                ),
            }
        )

    trailing = [item for item in ledger if item["outcome_group"] == "trailing_winner"]
    hard_stop = [item for item in ledger if item["outcome_group"] == "hard_stop_like_loss"]
    preserved_trailing = sum(not item["trigger_before_terminal"] for item in trailing)
    captured_stops = sum(item["trigger_before_terminal"] for item in hard_stop)
    trailing_preservation = (
        preserved_trailing / len(trailing) if trailing else None
    )
    stop_capture = captured_stops / len(hard_stop) if hard_stop else None
    informative = len(trailing) >= 4 and len(hard_stop) >= 4
    supported = bool(
        informative
        and trailing_preservation is not None
        and trailing_preservation >= 0.80
        and stop_capture is not None
        and stop_capture >= 0.50
    )
    summary = row["summary"]
    account_valid = bool(
        row["status"] == 0
        and summary.get("produced")
        and (summary.get("end_state") or {}).get("end_flat")
        and (summary.get("end_state") or {}).get(
            "closed_scenarios_match_closed_positions"
        )
    )
    return {
        "period": row["label"],
        "account_valid": account_valid,
        "source_account_summary": summary,
        "outcome_group_counts": dict(groups),
        "trailing_winner_preservation_share": trailing_preservation,
        "hard_stop_like_loss_capture_share": stop_capture,
        "captured_hard_stop_like_losses": captured_stops,
        "preserved_trailing_winners": preserved_trailing,
        "informative": informative,
        "prediction_supported": supported,
        "first_trigger_component_failure_counts": dict(component_failures),
        "trade_ledger": ledger,
    }


def render(result: dict[str, Any]) -> None:
    lines = [
        "# Winner15m lifecycle thesis forensic v3",
        "",
        f"- parity pass: {result['parity']['pass']}",
        f"- mechanically valid: {result['mechanically_valid']}",
        f"- decision: `{result['decision']}`",
        f"- thresholds searched: {result['thresholds_searched']}",
        f"- policy fresh authorized: {result['policy_fresh_authorized']}",
        "",
        "The tested transition is fixed: before trailing activation, the completed 15-minute public source side no longer matches the entry side while direction-adjusted close return is non-positive.",
        "",
        "| period | trades | trailing winners | hard-stop-like losses | winner preservation | loss capture | prediction supported |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, period in result.get("periods", {}).items():
        counts = period.get("outcome_group_counts") or {}
        source_summary = period.get("source_account_summary") or {}
        lines.append(
            f"| {name} | {source_summary.get('raw_trades')} | "
            f"{counts.get('trailing_winner', 0)} | "
            f"{counts.get('hard_stop_like_loss', 0)} | "
            f"{period.get('trailing_winner_preservation_share')} | "
            f"{period.get('hard_stop_like_loss_capture_share')} | "
            f"{period.get('prediction_supported')} |"
        )
    lines += [
        "",
        "No source entry, stop, target, trailing, ROI, score, risk, cost, fill or holding rule was changed. If the same categorical transition does not satisfy the predeclared separation in both consumed periods, the Winner lifecycle repair is rejected without retuning.",
    ]
    (EVIDENCE / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    if not FREEZE.is_file() or not REFERENCE.is_dir():
        raise RuntimeError("frozen specification or source-fidelity reference missing")
    if EVIDENCE.exists():
        shutil.rmtree(EVIDENCE)
    EVIDENCE.mkdir(parents=True, exist_ok=True)

    march = run_period("march_2025", PERIODS["march_2025"])
    parity = parity_check(march)
    dump(EVIDENCE / "parity.json", parity)
    if not parity.get("pass"):
        result = {
            "experiment": "candidate-57-winner-lifecycle-forensic-v3",
            "parity": parity,
            "mechanically_valid": False,
            "decision": "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION",
            "thresholds_searched": False,
            "policy_fresh_authorized": False,
            "integration_authorized": False,
            "long_evaluation_authorized": False,
            "periods": {},
        }
        dump(EVIDENCE / "analysis.json", result)
        render(result)
        return 2

    september = run_period("september_2024", PERIODS["september_2024"])
    periods = {
        "march_2025": analyze_period(march),
        "september_2024": analyze_period(september),
    }
    mechanically_valid = bool(
        parity.get("pass")
        and all(item["account_valid"] for item in periods.values())
    )
    supported = bool(
        mechanically_valid
        and all(item["prediction_supported"] for item in periods.values())
    )
    if not mechanically_valid:
        decision = "IMPLEMENTATION_ERROR_NO_ALPHA_CONCLUSION"
    elif supported:
        decision = "WINNER_SOURCE_THESIS_FAILURE_SUPPORTED_FRESH_REQUIRED"
    else:
        decision = "WINNER_LIFECYCLE_THESIS_HYPOTHESIS_REJECTED_NO_RETUNING"
    result = {
        "experiment": "candidate-57-winner-lifecycle-forensic-v3",
        "policy_changed": False,
        "parity": parity,
        "mechanically_valid": mechanically_valid,
        "decision": decision,
        "thresholds_searched": False,
        "policy_fresh_authorized": supported,
        "integration_authorized": False,
        "long_evaluation_authorized": False,
        "periods": periods,
    }
    dump(EVIDENCE / "analysis.json", result)
    for name, period in periods.items():
        dump(EVIDENCE / "periods" / f"{name}.json", period)
    render(result)
    print(json.dumps(safe(result), indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
