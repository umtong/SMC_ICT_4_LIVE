#!/usr/bin/env python3
"""Candidate 15 V43 evidence-locked positive-route portfolio integration.

The script does not invent a new entry rule.  It reads the frozen evidence and
candidate streams emitted by V33-V42, selects only source routes that were
cost-positive in both the declared development and year-long stability splits,
deduplicates the same symbol/30-minute causal episode, then applies one global
chronological position slot.  July 2026 and 2026-08-01..07 are the integration
confirmation slices.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


ENTRY_COLUMNS = (
    "entry_ts",
    "entry_time",
    "entry_timestamp",
    "confirmation_ts",
    "observed_ts",
    "observed_time",
)
EXIT_COLUMNS = (
    "exit_ts",
    "exit_time",
    "exit_timestamp",
    "outcome_ts",
    "horizon_ts",
)
EVENT_COLUMNS = (
    "event_ts",
    "decision_ts",
    "bucket_end_ts",
    "interaction_ts",
    "reentry_ts",
    "outside_ts",
    "day_start",
)
SYMBOL_COLUMNS = ("symbol", "instrument", "instrument_id")
ROUTE_COLUMNS = ("route", "family", "scenario", "module")
NET_COLUMNS = (
    "net_return",
    "after_cost_return",
    "net_ret",
    "net_pnl_return",
)
GROSS_COLUMNS = ("gross_return", "gross_ret", "raw_return")
RANK_COLUMNS = (
    "rank_score",
    "family_score",
    "shadow_score",
    "source_rank_score",
    "conservative_prediction",
    "score",
    "quality",
    "quality_score",
)


def load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def first_existing(paths: Iterable[str]) -> Path | None:
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            return path
    return None


def choose_column(
    frame: pd.DataFrame,
    names: Iterable[str],
    *,
    required: bool = True,
) -> str | None:
    lookup = {str(column).lower(): str(column) for column in frame.columns}
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    if required:
        raise KeyError(
            f"missing required column among {list(names)}; "
            f"columns={list(frame.columns)}",
        )
    return None


def parse_timestamp(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().mean() > 0.80 and numeric.notna().any():
        magnitude = float(numeric.dropna().abs().median())
        if magnitude >= 1e17:
            return pd.to_datetime(numeric, unit="ns", utc=True, errors="coerce")
        if magnitude >= 1e14:
            return pd.to_datetime(numeric, unit="us", utc=True, errors="coerce")
        if magnitude >= 1e11:
            return pd.to_datetime(numeric, unit="ms", utc=True, errors="coerce")
        if magnitude >= 1e9:
            return pd.to_datetime(numeric, unit="s", utc=True, errors="coerce")
    return pd.to_datetime(series, utc=True, errors="coerce")


def normalise_symbol(value: Any) -> str:
    text = str(value)
    text = text.replace("-PERP.BINANCE", "")
    text = text.replace(".BINANCE", "")
    return text


def existing_advance(root: Path) -> list[int]:
    versions: list[int] = []
    for status_name in (
        "V33_V38_REPLAY_STATUS.json",
        "V33_V37_ROUND_STATUS.json",
    ):
        status = root / status_name
        if status.is_file():
            payload = load_object(status)
            versions.extend(
                int(value) for value in payload.get("advance_versions", [])
            )
    for version in range(33, 43):
        for result in root.glob(f"V{version}_*_RESULT.md"):
            text = result.read_text(encoding="utf-8", errors="replace")
            if (
                "ADVANCE_TO_NAUTILUS" in text
                and "REJECTED_OR_UNDERPOWERED" not in text
                and "SKIPPED" not in text
            ):
                versions.append(version)
    return sorted(set(versions))


def split_route_stats(
    summary: dict[str, Any],
    split: str,
) -> tuple[dict[str, dict[str, Any]], str]:
    record = summary.get(split)
    if not isinstance(record, dict):
        return {}, "MISSING_SPLIT"
    route_stats = record.get("route_stats")
    if isinstance(route_stats, dict) and route_stats:
        return {
            str(key): value
            for key, value in route_stats.items()
            if isinstance(value, dict)
        }, "ROUTE_STATS"
    family_stats = record.get("family_stats")
    if isinstance(family_stats, dict) and family_stats:
        return {
            str(key): value
            for key, value in family_stats.items()
            if isinstance(value, dict)
        }, "FAMILY_STATS"
    return {}, "OVERALL_ONLY"


def read_raw_stream(version: int, spec: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidate_path = first_existing(spec["candidates"])
    if candidate_path is None:
        return pd.DataFrame(), {
            "version": version,
            "status": "MISSING_CANDIDATE_STREAM",
            "candidate_paths": spec["candidates"],
        }
    frame = pd.read_csv(candidate_path)
    if frame.empty:
        return pd.DataFrame(), {
            "version": version,
            "status": "EMPTY_CANDIDATE_STREAM",
            "candidate_path": str(candidate_path),
        }

    entry_col = choose_column(frame, ENTRY_COLUMNS)
    exit_col = choose_column(frame, EXIT_COLUMNS)
    symbol_col = choose_column(frame, SYMBOL_COLUMNS)
    route_col = choose_column(frame, ROUTE_COLUMNS, required=False)
    net_col = choose_column(frame, NET_COLUMNS)
    gross_col = choose_column(frame, GROSS_COLUMNS, required=False)
    rank_col = choose_column(frame, RANK_COLUMNS, required=False)
    event_col = choose_column(frame, EVENT_COLUMNS, required=False)

    output = pd.DataFrame(
        {
            "source_version": version,
            "source_path": str(candidate_path),
            "symbol": frame[symbol_col].map(normalise_symbol),
            "entry_ts": parse_timestamp(frame[entry_col]),
            "exit_ts": parse_timestamp(frame[exit_col]),
            "net_return": pd.to_numeric(frame[net_col], errors="coerce"),
        }
    )
    output["gross_return"] = (
        pd.to_numeric(frame[gross_col], errors="coerce")
        if gross_col is not None
        else output["net_return"]
    )
    output["source_rank_score"] = (
        pd.to_numeric(frame[rank_col], errors="coerce")
        if rank_col is not None
        else 1.0
    )
    output["source_route"] = (
        frame[route_col].astype(str)
        if route_col is not None
        else f"V{version}_SINGLE_ROUTE"
    )
    output["source_event_ts"] = (
        parse_timestamp(frame[event_col])
        if event_col is not None
        else output["entry_ts"]
    )
    output = output.dropna(
        subset=(
            "entry_ts",
            "exit_ts",
            "net_return",
            "gross_return",
            "source_event_ts",
        )
    )
    output = output[output["exit_ts"] > output["entry_ts"]].copy()
    output = output.drop_duplicates(
        ["source_version", "source_route", "symbol", "entry_ts", "exit_ts"],
        keep="first",
    )
    output = output.sort_values(
        ["entry_ts", "source_route", "symbol"],
        kind="stable",
    ).reset_index(drop=True)
    return output, {
        "version": version,
        "status": "LOADED",
        "candidate_path": str(candidate_path),
        "raw_rows": len(frame.index),
        "normalised_rows": len(output.index),
        "columns": {
            "entry": entry_col,
            "exit": exit_col,
            "event": event_col,
            "symbol": symbol_col,
            "route": route_col,
            "net": net_col,
            "gross": gross_col,
            "rank": rank_col,
        },
    }


def positive_routes_for_version(
    version: int,
    spec: dict[str, Any],
    selection: dict[str, Any],
    stream: pd.DataFrame,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    summary_path = Path(spec["summary"])
    if not summary_path.is_file():
        return [], {
            "version": version,
            "status": "MISSING_SUMMARY",
            "summary_path": str(summary_path),
        }
    summary = load_object(summary_path)
    if summary.get("advance_to_nautilus") is True:
        return [], {
            "version": version,
            "status": "ALREADY_ADVANCED",
            "classification": summary.get("classification"),
        }
    if "SKIPPED_EXISTING_NAUTILUS_ADVANCE" in str(
        summary.get("classification", "")
    ):
        return [], {
            "version": version,
            "status": "SKIPPED_SOURCE",
            "classification": summary.get("classification"),
        }

    development_stats, development_kind = split_route_stats(
        summary,
        "development",
    )
    stability_stats, stability_kind = split_route_stats(
        summary,
        "stability",
    )
    unique_routes = sorted(stream["source_route"].dropna().astype(str).unique())

    # A single-route family sometimes stores only overall split statistics.
    if not development_stats and len(unique_routes) == 1:
        record = summary.get("development", {})
        development_stats = {
            unique_routes[0]: {
                "trades": record.get("trades"),
                "mean_net_bps": record.get("mean_net_bps"),
                "net_t_stat": record.get("net_t_stat"),
                "win_rate": record.get("win_rate"),
            }
        }
        development_kind = "SINGLE_ROUTE_OVERALL"
    if not stability_stats and len(unique_routes) == 1:
        record = summary.get("stability", {})
        stability_stats = {
            unique_routes[0]: {
                "trades": record.get("trades"),
                "mean_net_bps": record.get("mean_net_bps"),
                "net_t_stat": record.get("net_t_stat"),
                "win_rate": record.get("win_rate"),
            }
        }
        stability_kind = "SINGLE_ROUTE_OVERALL"

    selected: list[dict[str, Any]] = []
    route_audit: dict[str, Any] = {}
    for route in sorted(set(development_stats) & set(stability_stats)):
        development = development_stats[route]
        stability = stability_stats[route]
        development_trades = int(development.get("trades") or 0)
        stability_trades = int(stability.get("trades") or 0)
        development_mean = development.get("mean_net_bps")
        stability_mean = stability.get("mean_net_bps")
        checks = {
            "minimum_development_trades": (
                development_trades
                >= int(selection["minimum_development_route_trades"])
            ),
            "minimum_stability_trades": (
                stability_trades
                >= int(selection["minimum_stability_route_trades"])
            ),
            "positive_development_mean": (
                development_mean is not None
                and float(development_mean)
                > float(selection["minimum_development_mean_net_bps"])
            ),
            "positive_stability_mean": (
                stability_mean is not None
                and float(stability_mean)
                > float(selection["minimum_stability_mean_net_bps"])
            ),
        }
        route_audit[route] = {
            "development": development,
            "stability": stability,
            "checks": checks,
        }
        if not all(checks.values()):
            continue
        score = min(float(development_mean), float(stability_mean))
        selected.append(
            {
                "source_version": version,
                "source_route": route,
                "route_score_bps": score,
                "development_trades": development_trades,
                "development_mean_net_bps": float(development_mean),
                "stability_trades": stability_trades,
                "stability_mean_net_bps": float(stability_mean),
            }
        )
    return selected, {
        "version": version,
        "status": "ROUTES_EVALUATED",
        "classification": summary.get("classification"),
        "summary_path": str(summary_path),
        "development_stat_kind": development_kind,
        "stability_stat_kind": stability_kind,
        "unique_stream_routes": unique_routes,
        "route_audit": route_audit,
        "selected_routes": selected,
    }


def attach_selected_routes(
    streams: list[pd.DataFrame],
    route_records: list[dict[str, Any]],
    bucket_minutes: int,
) -> pd.DataFrame:
    if not streams or not route_records:
        return pd.DataFrame()
    stream = pd.concat(streams, ignore_index=True)
    routes = pd.DataFrame(route_records)
    selected = stream.merge(
        routes,
        on=["source_version", "source_route"],
        how="inner",
        validate="many_to_one",
    )
    if selected.empty:
        return selected
    selected["source_rank_score"] = pd.to_numeric(
        selected["source_rank_score"],
        errors="coerce",
    ).fillna(0.0)
    selected["episode_bucket"] = selected["source_event_ts"].dt.floor(
        f"{bucket_minutes}min"
    )
    selected = selected.sort_values(
        [
            "episode_bucket",
            "symbol",
            "route_score_bps",
            "source_rank_score",
            "source_version",
            "source_route",
        ],
        ascending=[True, True, False, False, True, True],
        kind="stable",
    )
    selected["episode_rank"] = selected.groupby(
        ["symbol", "episode_bucket"],
        sort=False,
    ).cumcount()
    return selected


def deduplicate_and_arbitrate(
    proposals: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, Counter[str]]:
    if proposals.empty:
        return proposals.copy(), proposals.copy(), Counter()
    skips: Counter[str] = Counter()
    episode_winners = proposals[proposals["episode_rank"] == 0].copy()
    skips["SAME_SYMBOL_CAUSAL_EPISODE_DUPLICATE"] = int(
        (proposals["episode_rank"] > 0).sum()
    )
    episode_winners = episode_winners.sort_values(
        [
            "entry_ts",
            "route_score_bps",
            "source_rank_score",
            "source_version",
            "symbol",
        ],
        ascending=[True, False, False, True, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for entry_ts, group in episode_winners.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_ENTRY_LOSER"] += max(0, len(group.index) - 1)
        timestamp = pd.Timestamp(entry_ts)
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
    output = (
        pd.DataFrame(selected).reset_index(drop=True)
        if selected
        else episode_winners.iloc[0:0].copy()
    )
    if len(output.index) > 1:
        opened = pd.to_datetime(output["entry_ts"], utc=True)
        closed = pd.to_datetime(output["exit_ts"], utc=True)
        if not (
            opened.iloc[1:].reset_index(drop=True)
            >= closed.iloc[:-1].reset_index(drop=True)
        ).all():
            raise RuntimeError("global overlap survived V43 arbitration")
    return episode_winners, output, skips


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


def summarize(
    frame: pd.DataFrame,
    start: str,
    end: str,
) -> dict[str, Any]:
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
            "win_rate": None,
            "payoff_ratio": None,
            "net_t_stat": None,
            "positive_months": 0,
            "active_months": 0,
            "positive_month_share": 0.0,
            "route_counts": {},
            "symbol_counts": {},
            "version_counts": {},
        }
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    route_key = (
        "V"
        + sample["source_version"].astype(str)
        + ":"
        + sample["source_route"].astype(str)
    )
    return {
        "start": start,
        "end_exclusive": end,
        "calendar_days": days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(sample["net_return"].mean() * 10_000.0),
        "win_rate": float((sample["net_return"] > 0.0).mean()),
        "payoff_ratio": payoff(sample["net_return"]),
        "net_t_stat": t_stat(sample["net_return"]),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()),
        "route_counts": {
            str(key): int(value) for key, value in route_key.value_counts().items()
        },
        "symbol_counts": {
            str(key): int(value)
            for key, value in sample["symbol"].value_counts().items()
        },
        "version_counts": {
            str(key): int(value)
            for key, value in sample["source_version"].value_counts().items()
        },
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v43-positive-route-integration-v1":
        raise RuntimeError("unexpected V43 protocol")
    output.mkdir(parents=True, exist_ok=True)
    root = protocol_path.parent
    prior_advances = existing_advance(root)
    if prior_advances:
        summary = {
            "schema": "candidate-15-v43-summary-v1",
            "classification": "V43_SKIPPED_EXISTING_NAUTILUS_ADVANCE",
            "advance_to_nautilus": False,
            "existing_advance_versions": prior_advances,
            "decision": (
                "V43 was not evaluated because an earlier frozen route already "
                "requires immediate NautilusTrader promotion."
            ),
        }
        write_json(output / "summary.json", summary)
        (output / "RESULT.md").write_text(
            "# Candidate 15 V43\n\n"
            f"**{summary['classification']}**\n\n"
            f"- existing_advance_versions: `{prior_advances}`\n",
            encoding="utf-8",
        )
        return summary

    streams: list[pd.DataFrame] = []
    route_records: list[dict[str, Any]] = []
    source_audits: dict[str, Any] = {}
    selection = protocol["fixed_selection"]
    for version_text, spec in sorted(
        protocol["source_versions"].items(),
        key=lambda item: int(item[0]),
    ):
        version = int(version_text)
        stream, stream_audit = read_raw_stream(version, spec)
        streams.append(stream)
        selected_routes, route_audit = positive_routes_for_version(
            version,
            spec,
            selection,
            stream,
        )
        route_records.extend(selected_routes)
        source_audits[version_text] = {
            "stream": stream_audit,
            "route_selection": route_audit,
        }

    proposals = attach_selected_routes(
        streams,
        route_records,
        int(selection["episode_bucket_minutes"]),
    )
    episode_winners, selected, skips = deduplicate_and_arbitrate(proposals)
    write_json(
        output / "source_route_audit.json",
        {
            "schema": "candidate-15-v43-source-route-audit-v1",
            "source_audits": source_audits,
            "selected_route_records": route_records,
        },
    )
    proposals.to_csv(output / "all_positive_route_proposals.csv", index=False)
    episode_winners.to_csv(output / "episode_deduplicated_proposals.csv", index=False)
    selected.to_csv(output / "selected_integrated_trades.csv", index=False)

    evaluation = protocol["evaluation"]
    diagnostic = summarize(
        selected,
        evaluation["diagnostic_start"],
        evaluation["diagnostic_end_exclusive"],
    )
    july = summarize(
        selected,
        evaluation["july_confirmation_start"],
        evaluation["july_confirmation_end_exclusive"],
    )
    pulse = summarize(
        selected,
        evaluation["latest_pulse_start"],
        evaluation["latest_pulse_end_exclusive"],
    )
    combined_days = july["calendar_days"] + pulse["calendar_days"]
    combined_trades = july["trades"] + pulse["trades"]
    combined_rate = combined_trades / max(combined_days, 1)
    route_total = sum(july["route_counts"].values()) + sum(
        pulse["route_counts"].values()
    )
    symbol_total = sum(july["symbol_counts"].values()) + sum(
        pulse["symbol_counts"].values()
    )
    combined_route_counts = Counter(july["route_counts"])
    combined_route_counts.update(pulse["route_counts"])
    combined_symbol_counts = Counter(july["symbol_counts"])
    combined_symbol_counts.update(pulse["symbol_counts"])
    max_route_share = max(combined_route_counts.values(), default=0) / max(
        route_total,
        1,
    )
    max_symbol_share = max(combined_symbol_counts.values(), default=0) / max(
        symbol_total,
        1,
    )

    gate = protocol["advance_gate"]
    independent_routes = len(route_records)
    checks = {
        "minimum_independent_source_routes": (
            independent_routes
            >= int(gate["minimum_independent_source_routes"])
        ),
        "positive_july_mean_net": (
            july["mean_net_bps"] is not None and july["mean_net_bps"] > 0.0
        ),
        "minimum_july_trades": (
            july["trades"] >= int(gate["minimum_july_trades"])
        ),
        "minimum_july_net_t_stat": (
            july["net_t_stat"] is not None
            and july["net_t_stat"] >= float(gate["minimum_july_net_t_stat"])
        ),
        "positive_latest_pulse_mean_net": (
            pulse["mean_net_bps"] is not None
            and pulse["mean_net_bps"]
            >= float(gate["minimum_latest_pulse_mean_net_bps"])
        ),
        "minimum_latest_pulse_trades": (
            pulse["trades"] >= int(gate["minimum_latest_pulse_trades"])
        ),
        "minimum_confirmation_frequency": (
            combined_rate
            >= float(gate["minimum_confirmation_trades_per_day"])
        ),
        "route_concentration": (
            max_route_share <= float(gate["maximum_single_route_share"])
        ),
        "symbol_concentration": (
            max_symbol_share <= float(gate["maximum_single_symbol_share"])
        ),
    }
    advance = all(checks.values())
    classification = (
        "V43_POSITIVE_ROUTE_INTEGRATION_ADVANCE_TO_NAUTILUS"
        if advance
        else "V43_POSITIVE_ROUTE_INTEGRATION_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        "Freeze the selected route set, causal deduplication and arbitration "
        "unchanged, then implement all selected routes inside one "
        "NautilusTrader account."
        if advance
        else "The evidence-locked positive routes did not retain sufficient "
        "recent integrated expectancy and frequency. Do not relax source-route "
        "positivity, causal deduplication or confirmation gates."
    )
    summary = {
        "schema": "candidate-15-v43-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "selected_route_records": route_records,
        "independent_selected_routes": independent_routes,
        "all_positive_route_proposals": len(proposals.index),
        "episode_deduplicated_proposals": len(episode_winners.index),
        "selected_integrated_trades": len(selected.index),
        "arbitration_skips": dict(skips),
        "diagnostic": diagnostic,
        "july_confirmation": july,
        "latest_pulse": pulse,
        "combined_confirmation_days": combined_days,
        "combined_confirmation_trades": combined_trades,
        "combined_confirmation_trades_per_day": combined_rate,
        "combined_route_counts": dict(combined_route_counts),
        "combined_symbol_counts": dict(combined_symbol_counts),
        "maximum_confirmation_route_share": max_route_share,
        "maximum_confirmation_symbol_share": max_symbol_share,
        "advance_checks": checks,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V43 — Evidence-locked positive-route integration",
        "",
        f"**{classification}**",
        "",
        "Only V33-V42 source routes that were independently cost-positive in "
        "both development and year-long stability are admitted. Same-symbol "
        "30-minute causal episodes are deduplicated before one global position "
        "slot is applied.",
        "",
        f"- selected source routes: `{route_records}`",
        f"- proposals / episode winners / selected: "
        f"`{len(proposals.index)} / {len(episode_winners.index)} / "
        f"{len(selected.index)}`",
        "",
    ]
    for title, record in (
        ("Exposed diagnostic", diagnostic),
        ("July 2026 integration confirmation", july),
        ("Latest August 1-7 pulse", pulse),
    ):
        lines.extend(
            [
                f"## {title}",
                f"- interval: `{record['start']} -> {record['end_exclusive']}`",
                f"- trades / day: `{record['trades']} / "
                f"{record['trades_per_day']}`",
                f"- gross / net mean: `{record['mean_gross_bps']} / "
                f"{record['mean_net_bps']}` bp",
                f"- win rate / payoff: `{record['win_rate']} / "
                f"{record['payoff_ratio']}`",
                f"- net t-stat: `{record['net_t_stat']}`",
                f"- positive months: `{record['positive_months']} / "
                f"{record['active_months']}`",
                f"- route counts: `{record['route_counts']}`",
                f"- symbol counts: `{record['symbol_counts']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Combined confirmation",
            f"- trades / day: `{combined_trades} / {combined_rate}`",
            f"- route counts: `{dict(combined_route_counts)}`",
            f"- symbol counts: `{dict(combined_symbol_counts)}`",
            "",
            "## Advance checks",
            *[f"- {key}: `{value}`" for key, value in checks.items()],
            "",
            "## Decision",
            decision,
            "",
            "A pass is still a portfolio-mechanism promotion gate, not final "
            "success. The selected routes must be implemented online in "
            "NautilusTrader with current-NAV 3% risk, realistic costs and one "
            "continuous account.",
        ]
    )
    (output / "RESULT.md").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )
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
