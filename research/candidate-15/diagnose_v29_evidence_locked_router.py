#!/usr/bin/env python3
"""Merge evidence-locked V26 and V28 family proposal streams under one slot."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
import subprocess
from typing import Any

import pandas as pd


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def git_blob(path: Path) -> str:
    return subprocess.check_output(
        ["git", "hash-object", str(path)],
        text=True,
    ).strip()


def verify_locks(protocol: dict[str, Any]) -> dict[str, Any]:
    records: dict[str, Any] = {}
    failures: list[str] = []
    for family, record in protocol["locked_family_evidence"].items():
        path = Path(record["path"])
        actual = git_blob(path) if path.is_file() else None
        matched = actual == record["git_blob"]
        records[family] = {
            "path": str(path),
            "expected_git_blob": record["git_blob"],
            "actual_git_blob": actual,
            "matched": matched,
        }
        if not matched:
            failures.append(f"{family}: expected {record['git_blob']}, actual {actual}")
    if failures:
        raise RuntimeError("locked family evidence mismatch:\n" + "\n".join(failures))
    return {"schema": "candidate-15-v29-lock-v1", "all_matched": True, "families": records}


def load_family(
    family: str,
    record: dict[str, Any],
) -> pd.DataFrame:
    frame = pd.read_csv(record["path"])
    frame = frame[frame["route"] == record["route"]].copy()
    if frame.empty:
        return frame
    frame["entry_ts"] = pd.to_datetime(frame["entry_ts"], utc=True)
    frame["exit_ts"] = pd.to_datetime(frame["exit_ts"], utc=True)
    frame["family"] = family
    frame["family_priority"] = int(record["family_priority"])
    frame["family_rank"] = pd.to_numeric(frame["rank_score"], errors="raise")
    frame["net_return"] = pd.to_numeric(frame["net_return"], errors="raise")
    frame["gross_return"] = pd.to_numeric(frame["gross_return"], errors="raise")
    required = ["entry_ts", "exit_ts", "symbol", "route", "net_return", "gross_return"]
    if frame[required].isna().any().any():
        raise RuntimeError(f"{family} contains incomplete proposal evidence")
    if not (frame["exit_ts"] > frame["entry_ts"]).all():
        raise RuntimeError(f"{family} contains non-positive holding interval")
    duplicate = frame.duplicated(["family", "symbol", "entry_ts"]).any()
    if duplicate:
        raise RuntimeError(f"{family} duplicates a causal proposal")
    return frame


def arbitrate(frame: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    ordered = frame.sort_values(
        ["entry_ts", "family_priority", "family_rank", "symbol"],
        ascending=[True, False, False, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for timestamp, group in ordered.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_ENTRY_FAMILY_LOSER"] += max(0, len(group.index) - 1)
        if pd.Timestamp(timestamp) < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    if not selected:
        return ordered.iloc[0:0].copy(), skips
    output = pd.DataFrame(selected).sort_values("entry_ts", kind="stable")
    opens = output["entry_ts"].reset_index(drop=True)
    closes = output["exit_ts"].reset_index(drop=True)
    if len(output.index) > 1 and not (
        opens.iloc[1:].reset_index(drop=True)
        >= closes.iloc[:-1].reset_index(drop=True)
    ).all():
        raise RuntimeError("global overlap survived arbitration")
    return output.reset_index(drop=True), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins = values[values > 0.0]
    losses = values[values < 0.0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end, tz="UTC")
    sample = frame[
        (frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)
    ].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {
            "start": start,
            "end_exclusive": end,
            "calendar_days": days,
            "trades": 0,
            "trades_per_day": 0.0,
            "mean_gross_bps": None,
            "mean_net_bps": None,
            "net_t_stat": None,
            "win_rate": None,
            "payoff_ratio": None,
            "positive_months": 0,
            "active_months": 0,
            "positive_month_share": 0.0,
            "family_counts": {},
            "symbol_counts": {},
            "family_stats": {},
        }
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    family_stats: dict[str, Any] = {}
    for family, current in sample.groupby("family", sort=True):
        family_stats[str(family)] = {
            "trades": len(current.index),
            "mean_net_bps": float(current["net_return"].mean() * 10_000.0),
            "win_rate": float((current["net_return"] > 0.0).mean()),
            "payoff_ratio": payoff(current["net_return"]),
            "net_t_stat": t_stat(current["net_return"]),
        }
    return {
        "start": start,
        "end_exclusive": end,
        "calendar_days": days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "net_t_stat": t_stat(sample["net_return"]),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "payoff_ratio": payoff(sample["net_return"]),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()),
        "family_counts": {
            str(key): int(value)
            for key, value in sample["family"].value_counts().items()
        },
        "symbol_counts": {
            str(key): int(value)
            for key, value in sample["symbol"].value_counts().items()
        },
        "family_stats": family_stats,
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v29-evidence-locked-router-v1":
        raise RuntimeError("unexpected V29 protocol")
    output.mkdir(parents=True, exist_ok=True)
    lock = verify_locks(protocol)
    write_json(output / "source_lock.json", lock)

    families = [
        load_family(family, record)
        for family, record in protocol["locked_family_evidence"].items()
    ]
    proposals = pd.concat(
        [frame for frame in families if not frame.empty],
        ignore_index=True,
    )
    proposals.to_csv(output / "all_family_proposals.csv", index=False)
    selected, skips = arbitrate(proposals)
    selected.to_csv(output / "selected_integrated_trades.csv", index=False)

    evaluation = protocol["evaluation"]
    summaries = {
        name: summarize(
            selected,
            evaluation[f"{name}_start"],
            evaluation[f"{name}_end_exclusive"],
        )
        for name in ("development", "stability", "july_confirmation", "latest_pulse")
    }
    development = summaries["development"]
    stability = summaries["stability"]
    july = summaries["july_confirmation"]
    pulse = summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    stability_family_total = sum(stability["family_counts"].values())
    stability_symbol_total = sum(stability["symbol_counts"].values())
    max_family_share = (
        max(stability["family_counts"].values(), default=0)
        / max(stability_family_total, 1)
    )
    max_symbol_share = (
        max(stability["symbol_counts"].values(), default=0)
        / max(stability_symbol_total, 1)
    )
    checks = {
        "positive_development_mean_net": (
            development["mean_net_bps"] is not None
            and development["mean_net_bps"] > 0.0
        ),
        "positive_stability_mean_net": (
            stability["mean_net_bps"] is not None
            and stability["mean_net_bps"]
            >= float(gate["minimum_stability_mean_net_bps"])
        ),
        "stability_net_t_stat": (
            stability["net_t_stat"] is not None
            and stability["net_t_stat"]
            >= float(gate["minimum_stability_net_t_stat"])
        ),
        "stability_positive_month_share": (
            stability["positive_month_share"]
            >= float(gate["minimum_stability_positive_month_share"])
        ),
        "stability_frequency": (
            stability["trades_per_day"]
            >= float(gate["minimum_stability_trades_per_calendar_day"])
        ),
        "positive_july_confirmation_mean_net": (
            july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0
        ),
        "july_confirmation_trade_count": (
            july["trades"] >= int(gate["minimum_july_confirmation_trades"])
        ),
        "positive_latest_pulse_mean_net": (
            pulse["mean_net_bps"] is not None
            and pulse["mean_net_bps"]
            >= float(gate["minimum_latest_pulse_mean_net_bps"])
        ),
        "latest_pulse_trade_count": (
            pulse["trades"] >= int(gate["minimum_latest_pulse_trades"])
        ),
        "family_concentration": (
            max_family_share <= float(gate["maximum_single_family_share"])
        ),
        "symbol_concentration": (
            max_symbol_share <= float(gate["maximum_single_symbol_share"])
        ),
    }
    cross_split: dict[str, Any] = {}
    for family in protocol["locked_family_evidence"]:
        split_stats = {
            name: summary["family_stats"].get(family)
            for name, summary in summaries.items()
        }
        cross_split[family] = {
            "positive_across_all_declared_splits": all(
                record is not None and record["mean_net_bps"] > 0.0
                for record in split_stats.values()
            ),
            "splits": split_stats,
        }
    advance = all(checks.values())
    classification = (
        "V29_EVIDENCE_LOCKED_ROUTER_ADVANCE_TO_NAUTILUS"
        if advance
        else "V29_EVIDENCE_LOCKED_ROUTER_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        "Freeze the merged proposal policy and implement one NautilusTrader account."
        if advance
        else "The two positive historical families do not jointly satisfy recent "
        "confirmation and frequency. Preserve them only as rare components."
    )
    summary = {
        "schema": "candidate-15-v29-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "source_lock_passed": lock["all_matched"],
        "family_proposals": len(proposals.index),
        "selected_integrated_trades": len(selected.index),
        "arbitration_skips": dict(skips),
        **summaries,
        "advance_checks": checks,
        "cross_split_families": cross_split,
        "maximum_stability_family_share": max_family_share,
        "maximum_stability_symbol_share": max_symbol_share,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V29 — Evidence-locked multi-family router",
        "",
        f"**{classification}**",
        "",
        "The unchanged V26 OI-buildup and V28 breadth-trend proposal streams "
        "are merged chronologically under one global position slot.",
        "",
    ]
    for title, name in (
        ("Development", "development"),
        ("Year-long stability", "stability"),
        ("July 2026 confirmation", "july_confirmation"),
        ("Latest August 1-7 pulse", "latest_pulse"),
    ):
        record = summaries[name]
        lines.extend(
            [
                f"## {title}",
                f"- interval: `{record['start']} -> {record['end_exclusive']}`",
                f"- trades / day: `{record['trades']} / {record['trades_per_day']}`",
                f"- gross / net mean: `{record['mean_gross_bps']} / "
                f"{record['mean_net_bps']}` bp",
                f"- win rate / payoff: `{record['win_rate']} / "
                f"{record['payoff_ratio']}`",
                f"- net t-stat: `{record['net_t_stat']}`",
                f"- positive months: `{record['positive_months']} / "
                f"{record['active_months']}`",
                f"- family counts: `{record['family_counts']}`",
                f"- symbol counts: `{record['symbol_counts']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Advance checks",
            *[f"- {key}: `{value}`" for key, value in checks.items()],
            "",
            "## Decision",
            decision,
            "",
            "This integration does not synthesize account NAV. A pass still "
            "requires one frozen NautilusTrader continuous account with exact "
            "current-NAV 3% risk sizing and actual execution costs.",
        ]
    )
    (output / "RESULT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(args.protocol.resolve(), args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
