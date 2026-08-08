#!/usr/bin/env python3
"""Integrate timestamped positive alpha components from every candidate branch."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from io import StringIO
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any, Iterable

import numpy as np
import pandas as pd

ENTRY_COLUMNS = (
    "entry_ts", "entry_time", "entry_timestamp", "confirmation_ts",
    "observed_ts", "observed_time", "event_ts",
)
EXIT_COLUMNS = (
    "exit_ts", "exit_time", "exit_timestamp", "outcome_ts", "horizon_ts",
    "ts_closed", "close_ts",
)
EVENT_COLUMNS = (
    "event_ts", "decision_ts", "bucket_end_ts", "interaction_ts",
    "reentry_ts", "outside_ts", "observed_ts", "entry_ts",
)
SYMBOL_COLUMNS = ("symbol", "instrument", "instrument_id")
ROUTE_COLUMNS = ("route", "family", "scenario", "module", "setup")
NET_COLUMNS = (
    "net_return", "after_cost_return", "net_ret", "net_pnl_return",
)
GROSS_COLUMNS = ("gross_return", "gross_ret", "raw_return")
RANK_COLUMNS = (
    "rank_score", "family_score", "shadow_score", "source_rank_score",
    "conservative_prediction", "score", "quality", "quality_score",
)


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


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        text=True,
        errors="replace",
        stderr=subprocess.DEVNULL,
    )


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
        raise KeyError(f"missing {list(names)} in {list(frame.columns)}")
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
    for suffix in ("-PERP.BINANCE", ".BINANCE", "-PERP"):
        text = text.replace(suffix, "")
    return text


def existing_advance(root: Path) -> list[int]:
    versions: list[int] = []
    for status_name in (
        "V33_V38_REPLAY_STATUS.json",
        "V33_V37_ROUND_STATUS.json",
    ):
        path = root / status_name
        if path.is_file():
            versions.extend(
                int(value) for value in load_object(path).get("advance_versions", [])
            )
    for version in range(33, 44):
        for result in root.glob(f"V{version}_*_RESULT.md"):
            text = result.read_text(encoding="utf-8", errors="replace")
            if (
                "ADVANCE_TO_NAUTILUS" in text
                and "REJECTED_OR_UNDERPOWERED" not in text
                and "SKIPPED" not in text
            ):
                versions.append(version)
    return sorted(set(versions))


def select_components(
    scan: dict[str, Any],
    rules: dict[str, Any],
) -> list[dict[str, Any]]:
    route_records = [
        dict(record, component_kind="ROUTE")
        for record in scan.get("positive_route_components", [])
        if int(record.get("minimum_split_trades", 0))
        >= int(rules["minimum_component_split_trades"])
        and float(record.get("robust_score_bps", -math.inf))
        > float(rules["minimum_robust_score_bps"])
    ]
    overall_records = [
        dict(record, component_kind="SINGLE_FAMILY", route="__SINGLE_FAMILY__")
        for record in scan.get("positive_single_family_results", [])
        if int(record.get("minimum_split_trades", 0))
        >= int(rules["minimum_component_split_trades"])
        and float(record.get("robust_score_bps", -math.inf))
        > float(rules["minimum_robust_score_bps"])
    ]
    ordered = route_records + overall_records
    ordered.sort(
        key=lambda record: (
            0 if record["component_kind"] == "ROUTE" else 1,
            -float(record["robust_score_bps"]),
            -int(record["minimum_split_trades"]),
            int(record["candidate"]),
            str(record.get("route", "")),
        )
    )
    selected: list[dict[str, Any]] = []
    per_candidate: Counter[int] = Counter()
    seen: set[tuple[Any, ...]] = set()
    for record in ordered:
        candidate = int(record["candidate"])
        if per_candidate[candidate] >= int(rules["maximum_components_per_candidate"]):
            continue
        key = (
            candidate,
            record["path"],
            record.get("route"),
            record["development_split"],
            record["stability_split"],
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(record)
        per_candidate[candidate] += 1
        if len(selected) >= int(rules["maximum_components"]):
            break
    return selected


def ref_name(branch: str) -> str:
    return branch if branch.startswith("origin/") else f"origin/{branch}"


def tree_csvs(
    branch: str,
    summary_path: str,
    preferences: list[str],
    maximum_bytes: int,
) -> list[tuple[str, int]]:
    ref = ref_name(branch)
    parent = PurePosixPath(summary_path).parent
    roots = [str(parent)]
    if parent.parent != parent:
        roots.append(str(parent.parent))
    records: dict[str, int] = {}
    for root in roots:
        try:
            listing = git("ls-tree", "-rl", ref, root)
        except Exception:
            continue
        for line in listing.splitlines():
            try:
                metadata, path = line.split("\t", 1)
            except ValueError:
                continue
            parts = metadata.split()
            if len(parts) < 4 or not parts[3].isdigit():
                continue
            size = int(parts[3])
            name = PurePosixPath(path).name
            if (
                path.endswith(".csv")
                and 1 <= size <= maximum_bytes
                and name in preferences
            ):
                records[path] = size
    preference_index = {name: index for index, name in enumerate(preferences)}
    return sorted(
        records.items(),
        key=lambda item: (
            preference_index.get(PurePosixPath(item[0]).name, 999),
            len(PurePosixPath(item[0]).parts),
            item[0],
        ),
    )


def show_csv(branch: str, path: str) -> tuple[pd.DataFrame, str]:
    ref = ref_name(branch)
    text = git("show", f"{ref}:{path}")
    blob = git("rev-parse", f"{ref}:{path}").strip()
    return pd.read_csv(StringIO(text)), blob


def normalise_stream(
    frame: pd.DataFrame,
    component: dict[str, Any],
    source_path: str,
    blob: str,
) -> pd.DataFrame:
    entry_col = choose_column(frame, ENTRY_COLUMNS)
    exit_col = choose_column(frame, EXIT_COLUMNS)
    event_col = choose_column(frame, EVENT_COLUMNS, required=False)
    symbol_col = choose_column(frame, SYMBOL_COLUMNS)
    net_col = choose_column(frame, NET_COLUMNS)
    gross_col = choose_column(frame, GROSS_COLUMNS, required=False)
    rank_col = choose_column(frame, RANK_COLUMNS, required=False)

    route_col: str | None = None
    target_route = str(component.get("route", "__SINGLE_FAMILY__"))
    if component["component_kind"] == "ROUTE":
        for candidate in ROUTE_COLUMNS:
            column = choose_column(frame, (candidate,), required=False)
            if column is not None and (
                frame[column].astype(str) == target_route
            ).any():
                route_col = column
                break
        if route_col is None:
            return pd.DataFrame()
        frame = frame[frame[route_col].astype(str) == target_route].copy()
    else:
        route_col = choose_column(frame, ROUTE_COLUMNS, required=False)

    if frame.empty:
        return pd.DataFrame()
    output = pd.DataFrame(
        {
            "component_candidate": int(component["candidate"]),
            "component_kind": str(component["component_kind"]),
            "component_route": target_route,
            "source_branch": str(component["branch"]),
            "source_summary_path": str(component["path"]),
            "source_stream_path": source_path,
            "source_stream_blob": blob,
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
    output["source_event_ts"] = (
        parse_timestamp(frame[event_col])
        if event_col is not None
        else output["entry_ts"]
    )
    output["source_row_route"] = (
        frame[route_col].astype(str)
        if route_col is not None
        else target_route
    )
    output["component_score_bps"] = float(component["robust_score_bps"])
    output["component_minimum_split_trades"] = int(
        component["minimum_split_trades"]
    )
    output = output.dropna(
        subset=[
            "entry_ts", "exit_ts", "source_event_ts",
            "net_return", "gross_return",
        ]
    )
    output = output[output["exit_ts"] > output["entry_ts"]].copy()
    output = output.drop_duplicates(
        [
            "component_candidate", "component_route", "symbol",
            "entry_ts", "exit_ts",
        ],
        keep="first",
    )
    return output.reset_index(drop=True)


def load_component_stream(
    component: dict[str, Any],
    rules: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    candidates = tree_csvs(
        str(component["branch"]),
        str(component["path"]),
        list(rules["candidate_stream_preference"]),
        int(rules["maximum_candidate_stream_bytes"]),
    )
    attempts: list[dict[str, Any]] = []
    for path, size in candidates:
        try:
            frame, blob = show_csv(str(component["branch"]), path)
            normalised = normalise_stream(frame, component, path, blob)
            attempts.append(
                {
                    "path": path,
                    "bytes": size,
                    "blob": blob,
                    "raw_rows": len(frame.index),
                    "normalised_rows": len(normalised.index),
                }
            )
            if not normalised.empty:
                return normalised, {
                    "status": "LOADED",
                    "component": component,
                    "selected_path": path,
                    "selected_blob": blob,
                    "attempts": attempts,
                }
        except Exception as exc:
            attempts.append(
                {
                    "path": path,
                    "bytes": size,
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            )
    return pd.DataFrame(), {
        "status": "NO_COMPATIBLE_TIMESTAMPED_STREAM",
        "component": component,
        "attempts": attempts,
    }


def deduplicate_and_arbitrate(
    streams: list[pd.DataFrame],
    bucket_minutes: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, Counter[str]]:
    if not streams:
        empty = pd.DataFrame()
        return empty, empty, empty, Counter()
    proposals = pd.concat(streams, ignore_index=True)
    proposals["source_rank_score"] = pd.to_numeric(
        proposals["source_rank_score"], errors="coerce"
    ).fillna(0.0)
    proposals["episode_bucket"] = proposals["source_event_ts"].dt.floor(
        f"{bucket_minutes}min"
    )
    proposals = proposals.sort_values(
        [
            "episode_bucket", "symbol", "component_score_bps",
            "source_rank_score", "component_candidate", "component_route",
        ],
        ascending=[True, True, False, False, True, True],
        kind="stable",
    )
    proposals["episode_rank"] = proposals.groupby(
        ["symbol", "episode_bucket"], sort=False
    ).cumcount()
    episode = proposals[proposals["episode_rank"] == 0].copy()
    skips: Counter[str] = Counter(
        {
            "SAME_SYMBOL_CAUSAL_EPISODE_DUPLICATE": int(
                (proposals["episode_rank"] > 0).sum()
            )
        }
    )
    episode = episode.sort_values(
        [
            "entry_ts", "component_score_bps", "source_rank_score",
            "component_candidate", "component_route", "symbol",
        ],
        ascending=[True, False, False, True, True, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    free_at = pd.Timestamp.min.tz_localize("UTC")
    for entry_ts, group in episode.groupby("entry_ts", sort=True):
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
        if selected else episode.iloc[0:0].copy()
    )
    return proposals, episode, output, skips


def t_stat(values: pd.Series) -> float | None:
    if len(values.index) < 2:
        return None
    standard = float(values.std(ddof=1))
    if not math.isfinite(standard) or standard <= 0.0:
        return None
    return float(values.mean() / (standard / math.sqrt(len(values.index))))


def payoff(values: pd.Series) -> float | None:
    wins, losses = values[values > 0.0], values[values < 0.0]
    if wins.empty or losses.empty:
        return None
    return float(wins.mean() / abs(losses.mean()))


def summarize(frame: pd.DataFrame, start: str, end: str) -> dict[str, Any]:
    lower, upper = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
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
            "component_counts": {},
            "symbol_counts": {},
        }
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
    component = (
        "C"
        + sample["component_candidate"].astype(str)
        + ":"
        + sample["component_route"].astype(str)
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
        "component_counts": {
            str(key): int(value) for key, value in component.value_counts().items()
        },
        "symbol_counts": {
            str(key): int(value)
            for key, value in sample["symbol"].value_counts().items()
        },
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v44-cross-branch-component-integration-v1":
        raise RuntimeError("unexpected V44 protocol")
    output.mkdir(parents=True, exist_ok=True)
    root = protocol_path.parent
    prior_advances = existing_advance(root)
    if prior_advances:
        summary = {
            "schema": "candidate-15-v44-summary-v1",
            "classification": "V44_SKIPPED_EXISTING_NAUTILUS_ADVANCE",
            "advance_to_nautilus": False,
            "existing_advance_versions": prior_advances,
            "decision": "V44 was skipped because an earlier route already requires Nautilus promotion.",
        }
        write_json(output / "summary.json", summary)
        (output / "RESULT.md").write_text(
            f"# Candidate 15 V44\n\n**{summary['classification']}**\n\n"
            f"- existing_advance_versions: `{prior_advances}`\n",
            encoding="utf-8",
        )
        return summary

    scan = load_object(Path(protocol["source_scan"]))
    rules = protocol["fixed_selection"]
    selected_components = select_components(scan, rules)
    streams: list[pd.DataFrame] = []
    audits: list[dict[str, Any]] = []
    loaded_components: list[dict[str, Any]] = []
    for component in selected_components:
        stream, audit = load_component_stream(component, rules)
        audits.append(audit)
        if not stream.empty:
            streams.append(stream)
            loaded_components.append(component)

    proposals, episode, selected, skips = deduplicate_and_arbitrate(
        streams,
        int(rules["episode_bucket_minutes"]),
    )
    write_json(
        output / "source_component_lock.json",
        {
            "schema": "candidate-15-v44-source-component-lock-v1",
            "selected_components": selected_components,
            "loaded_components": loaded_components,
            "component_audits": audits,
        },
    )
    proposals.to_csv(output / "all_component_proposals.csv", index=False)
    episode.to_csv(output / "episode_deduplicated_proposals.csv", index=False)
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
    confirmation_days = july["calendar_days"] + pulse["calendar_days"]
    confirmation_trades = july["trades"] + pulse["trades"]
    confirmation_rate = confirmation_trades / max(confirmation_days, 1)
    component_counts = Counter(july["component_counts"])
    component_counts.update(pulse["component_counts"])
    symbol_counts = Counter(july["symbol_counts"])
    symbol_counts.update(pulse["symbol_counts"])
    max_component_share = max(component_counts.values(), default=0) / max(
        sum(component_counts.values()), 1
    )
    max_symbol_share = max(symbol_counts.values(), default=0) / max(
        sum(symbol_counts.values()), 1
    )

    gate = protocol["advance_gate"]
    checks = {
        "minimum_loaded_components": (
            len(loaded_components) >= int(gate["minimum_loaded_components"])
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
            confirmation_rate
            >= float(gate["minimum_combined_confirmation_trades_per_day"])
        ),
        "component_concentration": (
            max_component_share <= float(gate["maximum_single_component_share"])
        ),
        "symbol_concentration": (
            max_symbol_share <= float(gate["maximum_single_symbol_share"])
        ),
    }
    advance = all(checks.values())
    classification = (
        "V44_CROSS_BRANCH_COMPONENT_INTEGRATION_ADVANCE_TO_NAUTILUS"
        if advance
        else "V44_CROSS_BRANCH_COMPONENT_INTEGRATION_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        "Freeze the loaded cross-branch components, source blobs, causal "
        "deduplication and arbitration, then materialize their online logic in "
        "one NautilusTrader account."
        if advance
        else "The strongest timestamped cross-branch components did not retain "
        "sufficient July/August integrated expectancy and frequency. Do not "
        "lower component positivity, sample, deduplication or confirmation gates."
    )
    summary = {
        "schema": "candidate-15-v44-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "selected_components": selected_components,
        "loaded_components": loaded_components,
        "loaded_component_count": len(loaded_components),
        "all_component_proposals": len(proposals.index),
        "episode_deduplicated_proposals": len(episode.index),
        "selected_integrated_trades": len(selected.index),
        "arbitration_skips": dict(skips),
        "diagnostic": diagnostic,
        "july_confirmation": july,
        "latest_pulse": pulse,
        "combined_confirmation_days": confirmation_days,
        "combined_confirmation_trades": confirmation_trades,
        "combined_confirmation_trades_per_day": confirmation_rate,
        "combined_component_counts": dict(component_counts),
        "combined_symbol_counts": dict(symbol_counts),
        "maximum_confirmation_component_share": max_component_share,
        "maximum_confirmation_symbol_share": max_symbol_share,
        "advance_checks": checks,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V44 — Cross-branch positive-component integration",
        "",
        f"**{classification}**",
        "",
        "Every candidate branch was searched for route-level components that "
        "were cost-positive in two temporal splits. Their original timestamped "
        "streams are source-blob locked, same-symbol 30-minute episodes are "
        "deduplicated and one global chronological position slot is applied.",
        "",
        f"- selected components: `{selected_components}`",
        f"- loaded components: `{loaded_components}`",
        f"- proposals / episode winners / selected: "
        f"`{len(proposals.index)} / {len(episode.index)} / {len(selected.index)}`",
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
                f"- trades / day: `{record['trades']} / {record['trades_per_day']}`",
                f"- gross / net mean: `{record['mean_gross_bps']} / {record['mean_net_bps']}` bp",
                f"- win rate / payoff: `{record['win_rate']} / {record['payoff_ratio']}`",
                f"- net t-stat: `{record['net_t_stat']}`",
                f"- positive months: `{record['positive_months']} / {record['active_months']}`",
                f"- component counts: `{record['component_counts']}`",
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
            "A pass is a promotion gate, not final project success. Online logic "
            "must still run through exact NautilusTrader orders, current-NAV 3% "
            "risk and one continuous account.",
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
