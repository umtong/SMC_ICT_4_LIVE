#!/usr/bin/env python3
"""Pre-impulse multi-scale context audit for accepted forced unwinds.

The v61 lifecycle audit repairs slot release, market-episode independence and
the explicit REJECTED_15 management failure.  Its remaining loss concentration
is mechanistically consistent with a missing higher-timeframe question: is the
forced unwind continuing the prevailing repricing, or is it a liquidation
pullback against a broader auction?

This experiment does not search a lookback or threshold.  Before any result is
read it freezes two economically distinct clocks:

* 24 hours: the immediate three-funding-cycle/session context;
* 72 hours: the multi-day structural context.

Both clocks end at the close immediately before the one-hour impulse, so the
event cannot define its own context.  Alignment is only the sign of the
side-adjusted completed return.  Hourly price-path continuity and four-asset
breadth are recorded for anatomy, not optimized.

The primary hypothesis is that forced-unwind continuation should be retained
when both 24h and 72h completed contexts align with the event side.  The repair
is falsified if it merely deletes opportunities without specifically improving
the previously weak bull/post-publication states, or if ex-best expectancy
disappears.  This remains a path diagnostic, not final account evidence.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
HORIZONS_HOURS = (24, 72)
HERE = Path(__file__).resolve().parent
CONFIG = {
    "entry_mode": "direct",
    "stop_mode": "impulse_origin",
    "target_mode": "two_r",
    "hold_min": 480,
}
POLICY_ORDER = (
    "all_contexts_lifecycle_v61",
    "aligned_24h",
    "aligned_24h_and_72h",
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    raise TypeError(type(value))


def _hourly_close(minute: pd.DataFrame) -> pd.Series:
    work = minute.copy()
    work["close_time_dt"] = pd.to_datetime(
        work["close_time_dt"], utc=True, errors="coerce"
    )
    close_times = pd.DatetimeIndex(work["close_time_dt"])
    mask = close_times.minute.eq(59) & close_times.second.eq(59)
    hourly = (
        work.loc[mask, ["close_time_dt", "close"]]
        .drop_duplicates("close_time_dt", keep="last")
        .set_index("close_time_dt")["close"]
        .astype(float)
        .sort_index()
    )
    return hourly


def _window_metrics(
    hourly: pd.Series,
    *,
    origin_time: pd.Timestamp,
    horizon_hours: int,
    side: int,
) -> dict[str, Any] | None:
    start_time = origin_time - pd.Timedelta(hours=horizon_hours)
    window = hourly.loc[start_time:origin_time]
    if len(window) != horizon_hours + 1:
        return None
    times = pd.DatetimeIndex(window.index)
    if not bool(
        ((times[1:] - times[:-1]) == pd.Timedelta(hours=1)).all()
    ):
        return None
    prices = pd.to_numeric(window, errors="coerce").to_numpy(float)
    if not np.all(np.isfinite(prices)) or np.any(prices <= 0.0):
        return None
    log_prices = np.log(prices)
    raw_return = float(log_prices[-1] - log_prices[0])
    aligned_return = float(side * raw_return)
    path_length = float(np.abs(np.diff(log_prices)).sum())
    continuity = (
        None
        if path_length <= 0.0
        else float(aligned_return / path_length)
    )
    return {
        "raw_return": raw_return,
        "aligned_return": aligned_return,
        "aligned": bool(aligned_return > 0.0),
        "path_length": path_length,
        "path_continuity": continuity,
    }


def _event_context(
    *,
    event: pd.Series,
    hourly_by_symbol: dict[str, pd.Series],
) -> dict[str, Any] | None:
    side = int(event["side"])
    symbol = str(event["symbol"])
    event_time = pd.Timestamp(event["event_time"])
    # v59 impulse hour ends at event_time. Context ends one completed hour
    # earlier, matching the impulse-origin close used by the frozen stop.
    origin_time = event_time - pd.Timedelta(hours=1)
    output: dict[str, Any] = {"context_origin_time": origin_time}
    own: dict[int, dict[str, Any]] = {}
    for horizon in HORIZONS_HOURS:
        metrics = _window_metrics(
            hourly_by_symbol[symbol],
            origin_time=origin_time,
            horizon_hours=horizon,
            side=side,
        )
        if metrics is None:
            return None
        own[horizon] = metrics
        output[f"context_aligned_return_{horizon}h"] = metrics[
            "aligned_return"
        ]
        output[f"context_aligned_{horizon}h"] = metrics["aligned"]
        output[f"context_path_continuity_{horizon}h"] = metrics[
            "path_continuity"
        ]

        breadth = 0
        valid_assets = 0
        asset_returns: dict[str, float] = {}
        for peer in SYMBOLS:
            peer_metrics = _window_metrics(
                hourly_by_symbol[peer],
                origin_time=origin_time,
                horizon_hours=horizon,
                side=side,
            )
            if peer_metrics is None:
                continue
            valid_assets += 1
            breadth += int(peer_metrics["aligned"])
            asset_returns[peer] = float(peer_metrics["aligned_return"])
        if valid_assets != len(SYMBOLS):
            return None
        output[f"context_breadth_{horizon}h"] = breadth
        output[f"context_peer_returns_{horizon}h"] = json.dumps(
            asset_returns, sort_keys=True
        )

    output["context_regime"] = (
        "ALIGNED_24H_AND_72H"
        if own[24]["aligned"] and own[72]["aligned"]
        else "ALIGNED_24H_ONLY"
        if own[24]["aligned"]
        else "ALIGNED_72H_ONLY"
        if own[72]["aligned"]
        else "COUNTERTREND_24H_AND_72H"
    )
    return output


def run_one(args: argparse.Namespace) -> None:
    records = pd.read_csv(args.records, low_memory=False)
    mask = (
        records["period_label"].eq(args.period_label)
        & records["entry_mode"].eq(CONFIG["entry_mode"])
        & records["stop_mode"].eq(CONFIG["stop_mode"])
        & records["target_mode"].eq(CONFIG["target_mode"])
        & pd.to_numeric(records["hold_min"], errors="coerce").eq(
            CONFIG["hold_min"]
        )
    )
    events = records.loc[mask].copy()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if events.empty:
        payload = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "period_label": args.period_label,
            "split": args.split,
            "start": args.start,
            "end": args.end,
            "records": [],
            "opportunity_evidence": (
                "zero frozen forced-unwind geometry records in this period"
            ),
        }
        (output / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
        return

    for column in (
        "event_time",
        "entry_time_geometry",
        "exit_time_geometry",
        "delayed_entry_time",
    ):
        events[column] = pd.to_datetime(
            events[column], utc=True, errors="coerce"
        )
    events = events.sort_values(
        ["event_time", "symbol", "impulse_atr"],
        ascending=[True, True, False],
        kind="stable",
    ).drop_duplicates("causal_episode_id", keep="first")

    v59 = _load(
        HERE / "forced_unwind_geometry_v59_fixed.py",
        f"candidate51_v62_v59_{args.period_label}",
    )
    target = v59._load_target()
    target._contiguous = v59._contiguous

    start = date.fromisoformat(args.start) - timedelta(days=4)
    end = date.fromisoformat(args.end) + timedelta(days=1)
    hourly_by_symbol: dict[str, pd.Series] = {}
    source: dict[str, Any] = {}
    for symbol in SYMBOLS:
        minute, evidence, missing = target._load_observed_minutes(
            symbol=symbol,
            start=start,
            end=end,
            cache=Path(args.cache) / symbol,
            candidate05=Path(args.candidate05_path),
            candidate51=Path(args.candidate51_path),
        )
        hourly_by_symbol[symbol] = _hourly_close(minute)
        source[symbol] = {
            "evidence": evidence,
            "missing_close_times": [
                value.isoformat() for value in missing
            ],
        }

    enriched: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for _, event in events.iterrows():
        context = _event_context(
            event=event,
            hourly_by_symbol=hourly_by_symbol,
        )
        if context is None:
            invalid.append(
                {
                    "causal_episode_id": event["causal_episode_id"],
                    "symbol": event["symbol"],
                    "event_time": event["event_time"],
                    "reason": "incomplete_prior_context",
                }
            )
            continue
        enriched.append({**event.to_dict(), **context})

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "period_label": args.period_label,
        "split": args.split,
        "start": args.start,
        "end": args.end,
        "frozen_context": {
            "context_end": (
                "completed close immediately before the one-hour impulse"
            ),
            "horizons_hours": list(HORIZONS_HOURS),
            "alignment": "sign of side-adjusted completed return",
            "path_continuity": (
                "side-adjusted net hourly return divided by sum of absolute "
                "hourly returns; diagnostic only"
            ),
            "threshold_search": "none",
        },
        "source": source,
        "invalid_context_records": invalid,
        "records": enriched,
    }
    (output / "result.json").write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n"
    )


def _policy_frame(v61, frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    with_episodes = v61._assign_market_episodes(frame)
    candidates = v61._candidate_stream(with_episodes)
    return v61._select_policy(
        candidates,
        fixed_hold=False,
        episode_lock=True,
        rejection_exit=True,
    )


def _period_mean(result: dict[str, Any], period: str) -> float | None:
    value = result.get("by_period_r", {}).get(period, {}).get("mean")
    return None if value is None else float(value)


def aggregate(args: argparse.Namespace) -> None:
    paths = sorted(Path(args.results_root).rglob("result.json"))
    if not paths:
        raise RuntimeError("no result files")
    payloads = [json.loads(path.read_text()) for path in paths]
    frames = [
        pd.DataFrame(payload["records"])
        for payload in payloads
        if payload.get("records")
    ]
    if not frames:
        raise RuntimeError("no enriched context records")
    events = pd.concat(frames, ignore_index=True)
    for column in (
        "event_time",
        "entry_time_geometry",
        "exit_time_geometry",
        "delayed_entry_time",
        "context_origin_time",
    ):
        events[column] = pd.to_datetime(
            events[column], utc=True, errors="coerce"
        )

    v61 = _load(
        HERE / "forced_unwind_lifecycle_v61.py",
        "candidate51_v62_lifecycle",
    )

    policy_inputs = {
        "all_contexts_lifecycle_v61": events.copy(),
        "aligned_24h": events[
            events["context_aligned_24h"].eq(True)
        ].copy(),
        "aligned_24h_and_72h": events[
            events["context_aligned_24h"].eq(True)
            & events["context_aligned_72h"].eq(True)
        ].copy(),
    }
    results: dict[str, Any] = {}
    selected_outputs: list[pd.DataFrame] = []
    rejected_outputs: list[pd.DataFrame] = []
    selected_frames: dict[str, pd.DataFrame] = {}
    for name in POLICY_ORDER:
        selected, rejected = _policy_frame(v61, policy_inputs[name])
        selected["policy"] = name
        if not rejected.empty:
            rejected["policy"] = name
        results[name] = v61._policy_result(selected, rejected)
        selected_frames[name] = selected
        selected_outputs.append(selected)
        rejected_outputs.append(rejected)

    baseline = results["all_contexts_lifecycle_v61"]
    reproduced = (
        baseline["selected_trades"] == 40
        and abs(baseline["r_multiple"]["mean"] - 0.43209751596202495)
        < 1e-9
    )
    if not reproduced:
        raise RuntimeError(
            "v61 lifecycle reproduction failed; context attribution invalid"
        )

    primary = results["aligned_24h_and_72h"]
    baseline_bull = _period_mean(
        baseline, "untouched_bull_2024_03"
    )
    primary_bull = _period_mean(
        primary, "untouched_bull_2024_03"
    )
    baseline_post = _period_mean(baseline, "postpub_2026_07")
    primary_post = _period_mean(primary, "postpub_2026_07")

    assessment = {
        "v61_reproduced_exactly": reproduced,
        "ex_best_expectancy_remains_positive": (
            primary["r_multiple"].get("mean_without_best", -math.inf) > 0.0
        ),
        "bull_pullback_loss_state_improves": (
            primary_bull is not None
            and baseline_bull is not None
            and primary_bull > baseline_bull
        ),
        "latest_post_publication_state_improves": (
            primary_post is not None
            and baseline_post is not None
            and primary_post > baseline_post
        ),
        "primary_retains_nonzero_independent_opportunities": (
            primary["selected_trades"] > 0
        ),
    }

    regime_anatomy: dict[str, Any] = {}
    baseline_selected = selected_frames["all_contexts_lifecycle_v61"]
    for regime, group in baseline_selected.groupby(
        "context_regime", sort=True
    ):
        regime_anatomy[str(regime)] = v61._summary(group["policy_r"])

    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runs": len(payloads),
        "context_contract": {
            "24h": "immediate three-funding-cycle/session direction",
            "72h": "multi-day structural direction",
            "primary": (
                "continuation only when both completed contexts align with "
                "the forced-unwind side"
            ),
            "no_threshold_search": True,
            "path_continuity_and_breadth": "recorded for anatomy only",
        },
        "records": int(len(events)),
        "invalid_context_records": sum(
            len(payload.get("invalid_context_records", []))
            for payload in payloads
        ),
        "policies": results,
        "baseline_context_regime_anatomy": regime_anatomy,
        "hypothesis_assessment": assessment,
        "diagnostic_conclusion": (
            "multi_scale_alignment_supported"
            if all(assessment.values())
            else "multi_scale_alignment_not_fully_supported"
        ),
        "truth_boundary": (
            "Any supported context is a router component for one sparse "
            "specialist, not a final system. It still requires untouched "
            "confirmation and continuous NautilusTrader account validation."
        ),
    }

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "CONTEXT.json").write_text(
        json.dumps(
            payload,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n"
    )
    events.to_csv(output / "EVENTS.csv", index=False)
    pd.concat(selected_outputs, ignore_index=True).to_csv(
        output / "SELECTED.csv", index=False
    )
    nonempty = [frame for frame in rejected_outputs if not frame.empty]
    (
        pd.concat(nonempty, ignore_index=True)
        if nonempty
        else pd.DataFrame()
    ).to_csv(output / "REJECTED.csv", index=False)

    lines = [
        "# Forced-unwind pre-impulse context v62",
        "",
        f"- source periods: {len(payloads)}",
        f"- enriched records: {len(events)}",
        f"- v61 reproduction: `{reproduced}`",
        f"- conclusion: **{payload['diagnostic_conclusion']}**",
        "",
        "| policy | trades | trades/day | mean R | median R | PF | ex-best R | daily diagnostic geom | max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name in POLICY_ORDER:
        value = results[name]
        r = value["r_multiple"]
        nav = value["diagnostic_nav"]
        pf = r.get("profit_factor")
        lines.append(
            f"| {name} | {value['selected_trades']} | "
            f"{value['selected_trades_per_sampled_day']:.3f} | "
            f"{r.get('mean', 0.0):.3f} | {r.get('median', 0.0):.3f} | "
            f"{'na' if pf is None else f'{pf:.2f}'} | "
            f"{r.get('mean_without_best', 0.0):.3f} | "
            f"{100 * nav.get('daily_geometric_growth_over_140_sampled_days', 0.0):.3f}% | "
            f"{100 * nav.get('max_drawdown', 0.0):.2f}% |"
        )
    lines += ["", "## Predeclared assessment", ""]
    for key, value in assessment.items():
        lines.append(f"- {key}: `{value}`")
    lines += ["", "## Truth boundary", "", payload["truth_boundary"], ""]
    (output / "CONTEXT.md").write_text("\n".join(lines))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in (
        "period_label",
        "split",
        "start",
        "end",
        "records",
        "output",
    ):
        run.add_argument(
            f"--{name.replace('_', '-')}",
            dest=name,
            required=True,
        )
    run.add_argument(
        "--cache",
        default=".cache/candidate-51-forced-unwind-context-v62",
    )
    run.add_argument(
        "--candidate05-path",
        default="research/candidate-05",
    )
    run.add_argument(
        "--candidate51-path",
        default="research/candidate-51",
    )
    run.set_defaults(func=run_one)

    agg = sub.add_parser("aggregate")
    agg.add_argument("--results-root", required=True)
    agg.add_argument("--output", required=True)
    agg.set_defaults(func=aggregate)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
