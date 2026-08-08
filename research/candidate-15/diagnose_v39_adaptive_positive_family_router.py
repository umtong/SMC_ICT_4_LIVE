#!/usr/bin/env python3
"""Causally activate V17 OI buildup and merge with V28 breadth trend."""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

OI_FAMILY = "OI_POSITION_BUILDUP"
TREND_FAMILY = "BREADTH_6H_TREND"


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def first_existing(paths: Iterable[str]) -> Path:
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            return path
    raise FileNotFoundError(f"none of the candidate paths exist: {list(paths)}")


def choose_column(frame: pd.DataFrame, names: Iterable[str], required: bool = True) -> str | None:
    lowered = {str(column).lower(): str(column) for column in frame.columns}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    if required:
        raise KeyError(f"missing required column among {list(names)}; columns={list(frame.columns)}")
    return None


def parse_timestamp(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() > 0.9:
        magnitude = float(numeric.dropna().abs().median()) if numeric.notna().any() else 0.0
        if magnitude >= 1e17:
            return pd.to_datetime(numeric, unit="ns", utc=True)
        if magnitude >= 1e14:
            return pd.to_datetime(numeric, unit="us", utc=True)
        if magnitude >= 1e11:
            return pd.to_datetime(numeric, unit="ms", utc=True)
        if magnitude >= 1e9:
            return pd.to_datetime(numeric, unit="s", utc=True)
    return pd.to_datetime(series, utc=True, errors="coerce")


def load_stream(family: str, record: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    path = first_existing(record["candidate_paths"])
    frame = pd.read_csv(path)
    route_col = choose_column(frame, ("route", "scenario", "family", "module"), required=False)
    if route_col is not None:
        frame = frame[frame[route_col].astype(str) == str(record["route"])].copy()
    entry_col = choose_column(frame, ("entry_ts", "entry_time", "entry_timestamp", "observed_ts", "observed_time", "event_ts"))
    exit_col = choose_column(frame, ("exit_ts", "exit_time", "exit_timestamp", "outcome_ts", "horizon_ts"))
    symbol_col = choose_column(frame, ("symbol", "instrument", "instrument_id"))
    net_col = choose_column(frame, ("net_return", "after_cost_return", "net_ret", "net_pnl_return"))
    gross_col = choose_column(frame, ("gross_return", "gross_ret", "raw_return"), required=False)
    rank_col = choose_column(frame, ("rank_score", "score", "quality", "quality_score"), required=False)
    route_value = str(record["route"])
    output = pd.DataFrame({
        "family": family,
        "route": route_value,
        "symbol": frame[symbol_col].astype(str).str.replace("-PERP.BINANCE", "", regex=False),
        "entry_ts": parse_timestamp(frame[entry_col]),
        "exit_ts": parse_timestamp(frame[exit_col]),
        "net_return": pd.to_numeric(frame[net_col], errors="coerce"),
    })
    reserve = float(record.get("net_return_already_includes_bps", 0.0)) / 10_000.0
    output["gross_return"] = pd.to_numeric(frame[gross_col], errors="coerce") if gross_col else output["net_return"] + reserve
    output["source_rank_score"] = pd.to_numeric(frame[rank_col], errors="coerce") if rank_col else 1.0
    output["family_priority"] = int(record["family_priority"])
    output = output.dropna(subset=["entry_ts", "exit_ts", "net_return", "gross_return"])
    output = output[output["exit_ts"] > output["entry_ts"]].copy()
    output = output.drop_duplicates(["family", "symbol", "entry_ts"], keep="first")
    output = output.sort_values(["entry_ts", "symbol"], kind="stable").reset_index(drop=True)
    lock = {
        "family": family,
        "path": str(path),
        "rows": len(output.index),
        "columns": {"entry": entry_col, "exit": exit_col, "symbol": symbol_col, "net": net_col, "gross": gross_col, "rank": rank_col, "route": route_col},
    }
    return output, lock


def prior_stats(history: pd.DataFrame, entry: pd.Timestamp, days: int) -> dict[str, Any]:
    lower = entry - pd.Timedelta(days=days)
    sample = history[(history["exit_ts"] < entry) & (history["entry_ts"] >= lower)]
    if sample.empty:
        return {"events": 0, "mean_net_bps": None, "win_rate": None, "std_bps": None}
    return {
        "events": len(sample.index),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "std_bps": float(sample["net_return"].std(ddof=0) * 10_000.0),
    }


def activate_oi(stream: pd.DataFrame, rules: dict[str, Any]) -> tuple[pd.DataFrame, Counter[str]]:
    accepted: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    for _, row in stream.iterrows():
        entry = pd.Timestamp(row["entry_ts"])
        short = prior_stats(stream, entry, int(rules["short_lookback_days"]))
        long = prior_stats(stream, entry, int(rules["long_lookback_days"]))
        reasons: list[str] = []
        if short["events"] < int(rules["minimum_matured_events_short"]):
            reasons.append("SHORT_HISTORY")
        if long["events"] < int(rules["minimum_matured_events_long"]):
            reasons.append("LONG_HISTORY")
        if short["mean_net_bps"] is None or short["mean_net_bps"] < float(rules["minimum_short_mean_net_bps"]):
            reasons.append("SHORT_MEAN")
        if long["mean_net_bps"] is None or long["mean_net_bps"] < float(rules["minimum_long_mean_net_bps"]):
            reasons.append("LONG_MEAN")
        if short["win_rate"] is None or short["win_rate"] < float(rules["minimum_short_win_rate"]):
            reasons.append("SHORT_WIN_RATE")
        if reasons:
            skips.update(reasons)
            continue
        record = row.to_dict()
        record.update({
            "short_history_events": short["events"],
            "short_history_mean_net_bps": short["mean_net_bps"],
            "short_history_win_rate": short["win_rate"],
            "long_history_events": long["events"],
            "long_history_mean_net_bps": long["mean_net_bps"],
            "long_history_win_rate": long["win_rate"],
            "family_score": min(float(short["mean_net_bps"]), float(long["mean_net_bps"])) / max(float(short["std_bps"] or 1.0), float(long["std_bps"] or 1.0), 1.0),
            "activation": "CAUSAL_90D_180D_POSITIVE_HISTORY",
        })
        accepted.append(record)
    return (pd.DataFrame(accepted) if accepted else stream.iloc[0:0].copy()), skips


def prepare_trend(stream: pd.DataFrame) -> pd.DataFrame:
    output = stream.copy()
    rank = pd.to_numeric(output["source_rank_score"], errors="coerce").fillna(1.0)
    if rank.nunique() > 1:
        output["family_score"] = rank.rank(pct=True)
    else:
        output["family_score"] = 0.5
    output["activation"] = "FROZEN_INDEPENDENT_POSITIVE_FAMILY"
    return output


def arbitrate(frame: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    ordered = frame.sort_values(["entry_ts", "family_score", "family_priority", "symbol"], ascending=[True, False, False, True], kind="stable")
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for entry_ts, group in ordered.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_ENTRY_LOSER"] += max(0, len(group.index) - 1)
        timestamp = pd.Timestamp(entry_ts)
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    return (pd.DataFrame(selected).reset_index(drop=True) if selected else ordered.iloc[0:0].copy()), skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    std = float(values.std(ddof=1))
    if not math.isfinite(std) or std <= 0.0:
        return None
    return float(values.mean() / (std / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins, losses = values[values > 0.0], values[values < 0.0]
    return None if wins.empty or losses.empty else float(wins.mean() / abs(losses.mean()))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    sample = frame[(frame["entry_ts"] >= lower) & (frame["entry_ts"] < upper)].copy()
    days = int((upper - lower).total_seconds() // 86_400)
    if sample.empty:
        return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": 0, "trades_per_day": 0.0, "mean_gross_bps": None, "mean_net_bps": None, "win_rate": None, "payoff_ratio": None, "net_t_stat": None, "positive_months": 0, "active_months": 0, "positive_month_share": 0.0, "family_counts": {}, "symbol_counts": {}, "family_stats": {}}
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    family_stats: dict[str, Any] = {}
    for family, current in sample.groupby("family", sort=True):
        family_stats[str(family)] = {"trades": len(current.index), "mean_net_bps": float(current["net_return"].mean() * 10_000.0), "win_rate": float((current["net_return"] > 0.0).mean()), "payoff_ratio": payoff(current["net_return"]), "net_t_stat": t_stat(current["net_return"])}
    return {"start": start, "end_exclusive": end, "calendar_days": days, "trades": len(sample.index), "trades_per_day": len(sample.index) / max(days, 1), "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0), "mean_net_bps": float(sample["net_return"].mean() * 10_000.0), "win_rate": float((sample["net_return"] > 0.0).mean()), "payoff_ratio": payoff(sample["net_return"]), "net_t_stat": t_stat(sample["net_return"]), "positive_months": int((monthly > 0.0).sum()), "active_months": len(monthly.index), "positive_month_share": float((monthly > 0.0).mean()), "family_counts": {str(k): int(v) for k, v in sample["family"].value_counts().items()}, "symbol_counts": {str(k): int(v) for k, v in sample["symbol"].value_counts().items()}, "family_stats": family_stats}


def existing_advance(root: Path) -> list[int]:
    versions: list[int] = []
    status = root / "V33_V38_REPLAY_STATUS.json"
    if status.is_file():
        payload = load_object(status)
        versions.extend(int(value) for value in payload.get("advance_versions", []))
    for version in range(33, 39):
        for path in root.glob(f"V{version}_*_RESULT.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "ADVANCE_TO_NAUTILUS" in text and "REJECTED_OR_UNDERPOWERED" not in text:
                versions.append(version)
    return sorted(set(versions))


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v39-adaptive-positive-family-router-v1":
        raise RuntimeError("unexpected V39 protocol")
    output.mkdir(parents=True, exist_ok=True)
    root = protocol_path.parent
    prior_advances = existing_advance(root)
    if prior_advances:
        summary = {"schema": "candidate-15-v39-summary-v1", "classification": "V39_SKIPPED_EXISTING_NAUTILUS_ADVANCE", "advance_to_nautilus": False, "existing_advance_versions": prior_advances, "decision": "V39 was not evaluated because an earlier frozen route already requires immediate Nautilus promotion."}
        write_json(output / "summary.json", summary)
        (output / "RESULT.md").write_text(f"# Candidate 15 V39\n\n**{summary['classification']}**\n\n- existing_advance_versions: `{prior_advances}`\n", encoding="utf-8")
        return summary
    streams: dict[str, pd.DataFrame] = {}
    locks: dict[str, Any] = {}
    for family, record in protocol["locked_family_streams"].items():
        streams[family], locks[family] = load_stream(family, record)
    write_json(output / "source_lock.json", {"schema": "candidate-15-v39-source-stream-v1", "streams": locks})
    oi_active, activation_skips = activate_oi(streams[OI_FAMILY], protocol["causal_router"][OI_FAMILY])
    trend_active = prepare_trend(streams[TREND_FAMILY])
    proposals = pd.concat([oi_active, trend_active], ignore_index=True).sort_values(["entry_ts", "family"], kind="stable")
    selected, arbitration_skips = arbitrate(proposals)
    oi_active.to_csv(output / "active_oi_proposals.csv", index=False)
    proposals.to_csv(output / "all_active_family_proposals.csv", index=False)
    selected.to_csv(output / "selected_integrated_trades.csv", index=False)
    evaluation = protocol["evaluation"]
    summaries = {name: summarize(selected, evaluation[f"{name}_start"], evaluation[f"{name}_end_exclusive"]) for name in ("development", "stability", "july_confirmation", "latest_pulse")}
    development, stability, july, pulse = summaries["development"], summaries["stability"], summaries["july_confirmation"], summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    family_total, symbol_total = sum(stability["family_counts"].values()), sum(stability["symbol_counts"].values())
    max_family_share = max(stability["family_counts"].values(), default=0) / max(family_total, 1)
    max_symbol_share = max(stability["symbol_counts"].values(), default=0) / max(symbol_total, 1)
    checks = {
        "positive_development_mean_net": development["mean_net_bps"] is not None and development["mean_net_bps"] > 0.0,
        "positive_stability_mean_net": stability["mean_net_bps"] is not None and stability["mean_net_bps"] >= float(gate["minimum_stability_mean_net_bps"]),
        "stability_net_t_stat": stability["net_t_stat"] is not None and stability["net_t_stat"] >= float(gate["minimum_stability_net_t_stat"]),
        "stability_positive_month_share": stability["positive_month_share"] >= float(gate["minimum_stability_positive_month_share"]),
        "stability_frequency": stability["trades_per_day"] >= float(gate["minimum_stability_trades_per_calendar_day"]),
        "positive_july_confirmation_mean_net": july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0,
        "july_confirmation_trade_count": july["trades"] >= int(gate["minimum_july_confirmation_trades"]),
        "positive_latest_pulse_mean_net": pulse["mean_net_bps"] is not None and pulse["mean_net_bps"] >= float(gate["minimum_latest_pulse_mean_net_bps"]),
        "latest_pulse_trade_count": pulse["trades"] >= int(gate["minimum_latest_pulse_trades"]),
        "family_concentration": max_family_share <= float(gate["maximum_single_family_share"]),
        "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"]),
    }
    advance = all(checks.values())
    classification = "V39_ADAPTIVE_POSITIVE_FAMILY_ROUTER_ADVANCE_TO_NAUTILUS" if advance else "V39_ADAPTIVE_POSITIVE_FAMILY_ROUTER_REJECTED_OR_UNDERPOWERED"
    decision = "Freeze the causal family activation and implement the combined router in one NautilusTrader account." if advance else "The causal family router did not jointly pass recent confirmation and frequency. Do not tune its windows or activation thresholds."
    summary = {"schema": "candidate-15-v39-summary-v1", "classification": classification, "advance_to_nautilus": advance, "source_rows": {family: len(frame.index) for family, frame in streams.items()}, "active_oi_proposals": len(oi_active.index), "active_family_proposals": len(proposals.index), "selected_integrated_trades": len(selected.index), "activation_skips": dict(activation_skips), "arbitration_skips": dict(arbitration_skips), **summaries, "advance_checks": checks, "maximum_stability_family_share": max_family_share, "maximum_stability_symbol_share": max_symbol_share, "decision": decision}
    write_json(output / "summary.json", summary)
    lines = ["# Candidate 15 V39 — Adaptive positive-family router", "", f"**{classification}**", "", "The OI buildup family is activated only by fully matured positive 90-day and 180-day shadow outcomes; the rare breadth-trend family remains frozen and both compete under one chronological slot.", ""]
    for title, name in (("Development", "development"), ("Year-long stability", "stability"), ("July 2026 confirmation", "july_confirmation"), ("Latest August 1-7 pulse", "latest_pulse")):
        r = summaries[name]
        lines.extend([f"## {title}", f"- interval: `{r['start']} -> {r['end_exclusive']}`", f"- trades / day: `{r['trades']} / {r['trades_per_day']}`", f"- gross / net mean: `{r['mean_gross_bps']} / {r['mean_net_bps']}` bp", f"- win rate / payoff: `{r['win_rate']} / {r['payoff_ratio']}`", f"- net t-stat: `{r['net_t_stat']}`", f"- positive months: `{r['positive_months']} / {r['active_months']}`", f"- family counts: `{r['family_counts']}`", f"- symbol counts: `{r['symbol_counts']}`", ""])
    lines.extend(["## Advance checks", *[f"- {k}: `{v}`" for k, v in checks.items()], "", "## Decision", decision, "", "This is a causal evidence-stream integration screen. A pass still requires online shadow-state tracking and exact NautilusTrader continuous-account execution."])
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
