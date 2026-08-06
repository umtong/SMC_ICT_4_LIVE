"""Compile all committed candidate-06 evidence into one auditable terminal report."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable


STAGES: tuple[tuple[str, str], ...] = (
    ("unified_campaign", "artifacts/candidate-06/campaign/campaign.json"),
    ("followup", "artifacts/candidate-06/followup/followup.json"),
    ("breakthrough", "artifacts/candidate-06/breakthrough/breakthrough.json"),
    ("final_breakthrough", "artifacts/candidate-06/final-breakthrough/result.json"),
    ("terminal_stage", "artifacts/candidate-06/terminal-stage/terminal.json"),
)


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"parse_error": f"{type(exc).__name__}: {exc}"}
    return value if isinstance(value, dict) else {"invalid_top_level": type(value).__name__}


def _num(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        return f"{value:.10g}"
    if isinstance(value, (int, str)):
        return str(value)
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def _pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return _num(number)
    return f"{number:.6%}"


def _candidate_record(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    matrix = payload.get("matrix_summary") or {}
    record: dict[str, Any] = {
        "candidate": name,
        "test_rc": (payload.get("test_process") or {}).get("returncode"),
        "matrix_rc": (payload.get("matrix_process") or {}).get("returncode"),
        "selected_variant": payload.get("selected_variant", matrix.get("selected")),
        "three_weeks": payload.get("all_three_weeks_passed", matrix.get("all_three_weeks_passed")),
        "promotion_13w": (payload.get("promotion_13w") or {}).get("gate_passed"),
        "full_52w": (payload.get("full_52w") or {}).get("gate_passed"),
        "continuous": (payload.get("continuous") or {}).get("gate_passed"),
        "decision": payload.get("decision", payload.get("path")),
    }
    return record


def _flatten_candidates(stages: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    campaign = stages.get("unified_campaign") or {}
    for payload in campaign.get("candidate_results", []):
        if isinstance(payload, dict):
            rows.append(_candidate_record(str(payload.get("name", "unknown")), payload))
    followup = stages.get("followup") or {}
    if followup:
        rows.append(_candidate_record("session_equilibrium_retest_v5", followup))
    breakthrough = stages.get("breakthrough") or {}
    if breakthrough:
        rows.append(_candidate_record("session_liquidity_relay_v6", breakthrough))
    final = stages.get("final_breakthrough") or {}
    if final:
        rows.append(_candidate_record("rolling_auction_liquidity_relay_v7", final))
    terminal = stages.get("terminal_stage") or {}
    if terminal:
        rows.append(_candidate_record("multi_timescale_liquidity_relay_v8", terminal))
    return rows


def _walk(value: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_path = f"{path}[{index}]"
            yield from _walk(child, child_path)
    else:
        yield path, value


def _metrics_blocks(stages: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []

    def add(label: str, value: Any) -> None:
        if not isinstance(value, dict):
            return
        metric_keys = {
            "geometric_daily_nav_growth",
            "trades",
            "trades_per_day",
            "win_rate",
            "profit_factor",
            "max_drawdown_nav",
            "largest_positive_trade_share",
            "ending_nav",
            "gate_passed",
            "gate_failures",
        }
        if metric_keys.intersection(value):
            blocks.append({"label": label, **{key: value.get(key) for key in metric_keys}})

    def visit(value: Any, label: str) -> None:
        if isinstance(value, dict):
            add(label, value)
            for key, child in value.items():
                visit(child, f"{label}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{label}[{index}]")

    for stage_name, payload in stages.items():
        visit(payload, stage_name)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for block in blocks:
        signature = json.dumps(block, sort_keys=True, default=str)
        if signature not in seen:
            seen.add(signature)
            unique.append(block)
    return unique


def _terminal(stages: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    for name, _ in reversed(STAGES):
        payload = stages.get(name)
        if not payload:
            continue
        path = str(payload.get("path", ""))
        if path.startswith("WAITING_"):
            continue
        return name, payload
    return "none", {}


def _known_failures(stages: dict[str, dict[str, Any]]) -> list[str]:
    failures: set[str] = set()
    for path, value in _walk(stages):
        lowered = path.lower()
        if lowered.endswith("returncode") and value not in (None, 0):
            failures.add(f"IMPLEMENTATION_OR_RUNNER_RETURN_CODE: {path}={value}")
        if "gate_failures" in lowered and isinstance(value, str) and value:
            failures.add(value)
        if lowered.endswith("parse_error"):
            failures.add(f"EVIDENCE_PARSE_ERROR: {path}={value}")
        if lowered.endswith("error") and isinstance(value, str) and value:
            failures.add(f"RECORDED_ERROR: {value}")
    return sorted(failures)


def _render(
    *,
    stages: dict[str, dict[str, Any]],
    terminal_name: str,
    terminal: dict[str, Any],
    candidates: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    failures: list[str],
    revision: str,
) -> str:
    complete = bool(terminal.get("complete_candidate"))
    selected = terminal.get("selected_candidate")
    lines = [
        "# Candidate 06 — Final Research Status",
        "",
        f"- **Terminal evidence stage:** `{terminal_name}`",
        f"- **Complete candidate:** `{complete}`",
        f"- **Selected candidate:** `{selected}`",
        f"- **Terminal path:** `{terminal.get('path')}`",
        f"- **Evidence revision:** `{revision or 'unknown'}`",
        "",
        "A candidate is marked complete only when its causal state tests, three frozen BTC weeks, 13-week promotion, 52-week long screen, and single-engine continuous 2025 NautilusTrader confirmation all satisfy their committed gates. A partial pass is not promoted.",
        "",
        "## Candidate progression",
        "",
        "| candidate | state-test rc | matrix rc | selected variant | three frozen weeks | 13 weeks | 52 weeks | continuous 2025 | decision |",
        "|---|---:|---:|---|---|---|---|---|---|",
    ]
    for row in candidates:
        lines.append(
            "| `{candidate}` | `{test_rc}` | `{matrix_rc}` | `{selected_variant}` | `{three_weeks}` | `{promotion_13w}` | `{full_52w}` | `{continuous}` | `{decision}` |".format(
                **{key: _num(value) for key, value in row.items()}
            )
        )
    if not candidates:
        lines.append("| no candidate records | — | — | — | — | — | — | — | no committed result |")

    lines.extend(
        [
            "",
            "## Recorded performance blocks",
            "",
            "The labels below preserve the exact stage path from the committed JSON evidence.",
            "",
            "| evidence block | gate | geom. daily NAV | ending NAV | trades | trades/day | win rate | profit factor | max drawdown | largest-win share | failures |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for block in metrics:
        lines.append(
            "| `{label}` | `{gate}` | {growth} | `{ending}` | `{trades}` | `{tpd}` | {win} | `{pf}` | {dd} | {share} | `{failures}` |".format(
                label=block.get("label"),
                gate=_num(block.get("gate_passed")),
                growth=_pct(block.get("geometric_daily_nav_growth")),
                ending=_num(block.get("ending_nav")),
                trades=_num(block.get("trades")),
                tpd=_num(block.get("trades_per_day")),
                win=_pct(block.get("win_rate")),
                pf=_num(block.get("profit_factor")),
                dd=_pct(block.get("max_drawdown_nav")),
                share=_pct(block.get("largest_positive_trade_share")),
                failures=_num(block.get("gate_failures")),
            )
        )
    if not metrics:
        lines.append("| no metric blocks | — | — | — | — | — | — | — | — | — | — |")

    lines.extend(["", "## Known failure conditions", ""])
    lines.extend([f"- `{failure}`" for failure in failures] or ["- none recorded"])

    lines.extend(
        [
            "",
            "## Invariants retained throughout the research",
            "",
            "- All performance-producing weekly, 13-week, 52-week, and continuous-period runs use NautilusTrader. The orchestration scripts do not implement an alternative fill, order, portfolio, or PnL engine.",
            "- Planned loss is fixed at 3% of current portfolio NAV for every approved entry. Quantity is based on expected entry-to-stop loss plus the committed execution-cost assumptions.",
            "- The strategy permits at most one pending new-entry order or one open position. Exit/reduction orders are not counted as new entries.",
            "- Signals use completed bars and prior state only. Session/hour ownership is based on the source interval (`completed timestamp - 1 minute`).",
            "- The market-pattern detector, causal scenario state machine, order execution, and evidence recorder remain separate components.",
            "- Candidate selection is fixed-priority first-pass selection, never highest backtest return among a parameter grid.",
            "",
            "## Evidence map",
            "",
        ]
    )
    for stage_name, relative in STAGES:
        status = "present" if stage_name in stages else "missing"
        lines.append(f"- `{relative}` — {status}")
    lines.extend(
        [
            "- `research/candidate-06/config*.locked.json` — immutable configuration for any candidate promoted beyond the first frozen week.",
            "- Scenario transitions, orders, positions, trades, equity, manifests, and error logs remain under the corresponding artifact directories.",
            "",
            "## Terminal decision",
            "",
        ]
    )
    if complete:
        lines.append(
            f"`{selected}` is the completed candidate under the committed gates. Its exact locked configuration and all promotion/continuous evidence are referenced above; no risk or size was reduced to force performance toward 1%."
        )
    else:
        lines.append(
            "No implemented candidate is represented as complete. The branch preserves each failed causal hypothesis and its exact failure stage so later research can start from diagnosed evidence rather than repeat the same pattern or tune parameters to the target."
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parent.parent.parent)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "FINAL_STATUS.md")
    parser.add_argument("--json-output", type=Path, default=Path(__file__).resolve().parent / "FINAL_STATUS.json")
    args = parser.parse_args()
    repository = args.repository.resolve()
    stages: dict[str, dict[str, Any]] = {}
    stage_manifest: dict[str, Any] = {}
    for name, relative in STAGES:
        value = _load(repository / relative)
        if value is not None:
            stages[name] = value
        stage_manifest[name] = {"path": relative, "present": value is not None}

    terminal_name, terminal = _terminal(stages)
    candidates = _flatten_candidates(stages)
    metrics = _metrics_blocks(stages)
    failures = _known_failures(stages)
    revision = os.environ.get("GITHUB_SHA", "")
    status = {
        "schema_version": 1,
        "evidence_revision": revision or None,
        "terminal_evidence_stage": terminal_name,
        "terminal_path": terminal.get("path"),
        "complete_candidate": bool(terminal.get("complete_candidate")),
        "selected_candidate": terminal.get("selected_candidate"),
        "candidate_progression": candidates,
        "known_failure_conditions": failures,
        "evidence_manifest": stage_manifest,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        _render(
            stages=stages,
            terminal_name=terminal_name,
            terminal=terminal,
            candidates=candidates,
            metrics=metrics,
            failures=failures,
            revision=revision,
        ),
        encoding="utf-8",
    )
    args.json_output.write_text(json.dumps(status, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0 if status["complete_candidate"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
