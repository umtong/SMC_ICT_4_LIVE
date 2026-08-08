#!/usr/bin/env python3
"""Candidate 15 V27 daily option-expiry hedge-release screen.

The state is known at 08:00 UTC: a focal four-hour move, cross-market residual,
volume, futures open-interest buildup and the 07:30-08:00 delivery-window
pressure.  A wholly subsequent 08:00-08:15 price/flow reversal confirms a new
post-expiry auction leg. Entry is the 08:15 close, invalidation is the combined
07:30-08:15 extreme, and the target is the pre-expiry four-hour volume-weighted
equilibrium.

Binance futures OI is explicitly a positioning proxy; no claim is made that it
measures Deribit option gamma. This is a causal mechanism/geometry screen, not
a portfolio simulator. A pass still requires NautilusTrader.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_v17_open_interest as oi_base
import diagnose_v23_quarter_hour_10s as price_base
import run_v17_open_interest as metrics_adapter

TRADE_SYMBOLS = ("BTCUSDT", "ETHUSDT")
CONTEXT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
ROUTE = "DAILY_EXPIRY_HEDGE_RELEASE_4H"


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


def load_five_minute(
    symbol: str,
    protocol: dict[str, Any],
    data_dir: Path,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    data = protocol["data"]
    start = date.fromisoformat(data["start"])
    end = date.fromisoformat(data["end_exclusive"])
    monthly_end = date(2026, 8, 1)
    frames: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []
    for month in price_base.month_starts(start, min(monthly_end, end)):
        token = f"{month.year:04d}-{month.month:02d}"
        filename = f"{symbol}-5m-{token}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/monthly/klines/"
            f"{symbol}/5m/{filename}"
        )
        path = data_dir / "prices" / symbol / filename
        record = price_base.download(url, path)
        raw = price_base.read_kline_archive(path)
        record.update(
            {
                "symbol": symbol,
                "dataset": "futures_5m_klines",
                "token": token,
                "rows": len(raw.index),
            }
        )
        frames.append(raw)
        manifest.append(record)
    for day in price_base.daterange(max(start, monthly_end), end):
        filename = f"{symbol}-5m-{day.isoformat()}.zip"
        url = (
            "https://data.binance.vision/data/futures/um/daily/klines/"
            f"{symbol}/5m/{filename}"
        )
        path = data_dir / "prices" / symbol / filename
        record = price_base.download(url, path)
        raw = price_base.read_kline_archive(path)
        record.update(
            {
                "symbol": symbol,
                "dataset": "futures_5m_klines",
                "token": day.isoformat(),
                "rows": len(raw.index),
            }
        )
        frames.append(raw)
        manifest.append(record)
    frame = price_base.normalize_kline(pd.concat(frames, ignore_index=True), 300)
    lower = pd.Timestamp(start, tz="UTC")
    upper = pd.Timestamp(end, tz="UTC")
    frame = frame[(frame.index > lower) & (frame.index <= upper)]
    expected = int((upper - lower).total_seconds() // 300)
    coverage = len(frame.index) / max(expected, 1)
    if coverage < 0.995:
        raise RuntimeError(
            f"insufficient {symbol} five-minute coverage "
            f"{len(frame.index)}/{expected} ({coverage:.6f})"
        )
    quote = frame["quote_volume"].astype(float)
    taker = frame["taker_buy_quote_volume"].astype(float)
    frame["signed_taker_pressure"] = (
        2.0 * taker / quote.replace(0.0, np.nan) - 1.0
    ).clip(-1.0, 1.0)
    return frame.replace([np.inf, -np.inf], np.nan), manifest


def weighted_pressure(frame: pd.DataFrame) -> float:
    weights = np.maximum(frame["quote_volume"].astype(float).to_numpy(), 1.0)
    values = frame["signed_taker_pressure"].astype(float).to_numpy()
    return float(np.average(values, weights=weights))


def volume_weighted_close(frame: pd.DataFrame) -> float:
    weights = np.maximum(frame["quote_volume"].astype(float).to_numpy(), 1.0)
    return float(np.average(frame["close"].astype(float).to_numpy(), weights=weights))


def bars_between(
    frame: pd.DataFrame,
    start_exclusive: pd.Timestamp,
    end_inclusive: pd.Timestamp,
) -> pd.DataFrame:
    return frame[(frame.index > start_exclusive) & (frame.index <= end_inclusive)]


def daily_raw_events(
    prices: dict[str, pd.DataFrame],
    metrics: dict[str, pd.DataFrame],
    protocol: dict[str, Any],
) -> pd.DataFrame:
    rules = protocol["fixed_rules"]
    data = protocol["data"]
    start = date.fromisoformat(data["start"])
    end = date.fromisoformat(data["end_exclusive"])
    records: list[dict[str, Any]] = []
    for timestamp in pd.date_range(start, end, freq="D", inclusive="left", tz="UTC"):
        expiry = timestamp + pd.Timedelta(hours=8)
        pre_start = expiry - pd.Timedelta(minutes=int(rules["pre_expiry_minutes"]))
        delivery_start = expiry - pd.Timedelta(minutes=int(rules["delivery_twap_minutes"]))
        confirmation_end = expiry + pd.Timedelta(minutes=int(rules["confirmation_minutes"]))
        outcome_end = confirmation_end + pd.Timedelta(minutes=int(rules["outcome_horizon_minutes"]))
        context_moves: dict[str, float] = {}
        complete_context = True
        for symbol in CONTEXT_SYMBOLS:
            window = bars_between(prices[symbol], pre_start, expiry)
            if len(window.index) != int(rules["pre_expiry_minutes"]) // 5:
                complete_context = False
                break
            context_moves[symbol] = float(
                np.log(float(window["close"].iloc[-1]) / float(window["open"].iloc[0]))
            )
        if not complete_context:
            continue
        factor_move = float(np.median(list(context_moves.values())))
        for symbol in TRADE_SYMBOLS:
            pre = bars_between(prices[symbol], pre_start, expiry)
            delivery = bars_between(prices[symbol], delivery_start, expiry)
            confirmation = bars_between(prices[symbol], expiry, confirmation_end)
            path = bars_between(prices[symbol], confirmation_end, outcome_end)
            metric_pre = bars_between(metrics[symbol], pre_start, expiry)
            metric_confirmation = bars_between(metrics[symbol], expiry, confirmation_end)
            expected = (
                len(pre.index) == int(rules["pre_expiry_minutes"]) // 5
                and len(delivery.index) == int(rules["delivery_twap_minutes"]) // 5
                and len(confirmation.index) == int(rules["confirmation_minutes"]) // 5
                and len(path.index) == int(rules["outcome_horizon_minutes"]) // 5
                and len(metric_pre.index) >= int(rules["pre_expiry_minutes"]) // 5 - 1
                and len(metric_confirmation.index) >= int(rules["confirmation_minutes"]) // 5 - 1
            )
            if not expected:
                continue
            pre_move = context_moves[symbol]
            residual = pre_move - factor_move
            pre_volume = float(pre["quote_volume"].sum())
            pre_pressure = weighted_pressure(pre)
            delivery_return = float(
                float(delivery["close"].iloc[-1]) / float(delivery["open"].iloc[0]) - 1.0
            )
            delivery_pressure = weighted_pressure(delivery)
            confirmation_return = float(
                float(confirmation["close"].iloc[-1])
                / float(confirmation["open"].iloc[0])
                - 1.0
            )
            confirmation_pressure = weighted_pressure(confirmation)
            open_interest = metric_pre["sum_open_interest"].astype(float).replace(0.0, np.nan)
            if open_interest.dropna().empty:
                continue
            pre_oi_change = float(
                np.log(float(open_interest.dropna().iloc[-1]) / float(open_interest.dropna().iloc[0]))
            )
            confirmation_oi = metric_confirmation["sum_open_interest"].astype(float).replace(0.0, np.nan)
            if confirmation_oi.dropna().empty:
                continue
            confirmation_oi_change = float(
                np.log(
                    float(confirmation_oi.dropna().iloc[-1])
                    / float(open_interest.dropna().iloc[-1])
                )
            )
            entry_price = float(confirmation["close"].iloc[-1])
            equilibrium = volume_weighted_close(pre)
            combined = pd.concat([delivery, confirmation]).sort_index()
            records.append(
                {
                    "expiry_date": timestamp.date().isoformat(),
                    "expiry_ts": expiry,
                    "symbol": symbol,
                    "pre_start": pre_start,
                    "delivery_start": delivery_start,
                    "confirmation_end": confirmation_end,
                    "outcome_end": outcome_end,
                    "pre_move": pre_move,
                    "factor_move": factor_move,
                    "cross_market_residual": residual,
                    "residual_share": abs(residual) / max(abs(pre_move), 1e-12),
                    "pre_quote_volume": pre_volume,
                    "pre_taker_pressure": pre_pressure,
                    "pre_oi_change": pre_oi_change,
                    "delivery_return": delivery_return,
                    "delivery_taker_pressure": delivery_pressure,
                    "confirmation_return": confirmation_return,
                    "confirmation_taker_pressure": confirmation_pressure,
                    "confirmation_oi_change": confirmation_oi_change,
                    "entry_ts": confirmation_end,
                    "entry_price": entry_price,
                    "equilibrium_target": equilibrium,
                    "combined_high": float(combined["high"].max()),
                    "combined_low": float(combined["low"].min()),
                }
            )
    frame = pd.DataFrame(records)
    if frame.empty:
        return frame
    for column in (
        "expiry_ts",
        "pre_start",
        "delivery_start",
        "confirmation_end",
        "outcome_end",
        "entry_ts",
    ):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame.sort_values(["expiry_ts", "symbol"], kind="stable").reset_index(drop=True)


def prior_daily_z(
    frame: pd.DataFrame,
    column: str,
    window: int,
    minimum: int,
) -> pd.Series:
    values = frame[column].astype(float)
    prior = values.shift(1)
    mean = prior.rolling(window, min_periods=minimum).mean()
    standard = prior.rolling(window, min_periods=minimum).std(ddof=0).replace(0.0, np.nan)
    return (values - mean) / standard


def add_prior_state(raw: pd.DataFrame, protocol: dict[str, Any]) -> pd.DataFrame:
    if raw.empty:
        return raw.copy()
    rules = protocol["fixed_rules"]
    window = int(rules["prior_daily_events"])
    minimum = int(rules["minimum_prior_daily_events"])
    parts: list[pd.DataFrame] = []
    for symbol, group in raw.groupby("symbol", sort=True):
        group = group.sort_values("expiry_ts", kind="stable").copy()
        group["pre_return_z"] = prior_daily_z(group, "pre_move", window, minimum)
        group["residual_z"] = prior_daily_z(
            group, "cross_market_residual", window, minimum
        )
        group["pre_volume_z"] = prior_daily_z(
            group.assign(log_volume=np.log1p(group["pre_quote_volume"])),
            "log_volume",
            window,
            minimum,
        )
        group["pre_oi_change_z"] = prior_daily_z(
            group, "pre_oi_change", window, minimum
        )
        group["confirmation_oi_change_z"] = prior_daily_z(
            group, "confirmation_oi_change", window, minimum
        )
        parts.append(group)
    return pd.concat(parts, ignore_index=True).sort_values(
        ["expiry_ts", "symbol"], kind="stable"
    )


def first_passage(
    price: pd.DataFrame,
    event: pd.Series,
    *,
    direction: int,
    stop: float,
    target: float,
) -> dict[str, Any] | None:
    path = bars_between(
        price,
        pd.Timestamp(event["entry_ts"]),
        pd.Timestamp(event["outcome_end"]),
    )
    if path.empty:
        return None
    exit_price: float | None = None
    exit_ts: pd.Timestamp | None = None
    exit_reason = "EXPIRY_WINDOW_TIMEOUT"
    ambiguous = False
    for timestamp, bar in path.iterrows():
        if direction > 0:
            stop_hit = float(bar["low"]) <= stop
            target_hit = float(bar["high"]) >= target
        else:
            stop_hit = float(bar["high"]) >= stop
            target_hit = float(bar["low"]) <= target
        if stop_hit and target_hit:
            ambiguous = True
            exit_price = stop
            exit_ts = pd.Timestamp(timestamp)
            exit_reason = "STOP_FIRST_SAME_BAR_AMBIGUITY"
            break
        if stop_hit:
            exit_price = stop
            exit_ts = pd.Timestamp(timestamp)
            exit_reason = "DELIVERY_CONFIRMATION_EXTREME_STOP"
            break
        if target_hit:
            exit_price = target
            exit_ts = pd.Timestamp(timestamp)
            exit_reason = "PRE_EXPIRY_VWAP_TARGET"
            break
    if exit_price is None:
        exit_price = float(path["close"].iloc[-1])
        exit_ts = pd.Timestamp(path.index[-1])
    gross_return = direction * (
        exit_price / float(event["entry_price"]) - 1.0
    )
    return {
        "exit_ts": exit_ts,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "same_bar_ambiguous": ambiguous,
        "gross_return": gross_return,
    }


def classify(
    frame: pd.DataFrame,
    prices: dict[str, pd.DataFrame],
    protocol: dict[str, Any],
) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    rules = protocol["fixed_rules"]
    required = [
        "pre_return_z",
        "residual_z",
        "pre_volume_z",
        "pre_oi_change_z",
        "confirmation_oi_change_z",
    ]
    events = frame.dropna(subset=required).copy()
    rejected: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    cost_return = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    for _, event in events.iterrows():
        pre_direction = int(np.sign(float(event["pre_move"])))
        if pre_direction == 0:
            rejected["NO_PRE_EXPIRY_DIRECTION"] += 1
            continue
        checks = {
            "PRE_RETURN_NOT_EXTREME": abs(float(event["pre_return_z"]))
            >= float(rules["absolute_pre_return_z_min"]),
            "RESIDUAL_NOT_EXTREME": abs(float(event["residual_z"]))
            >= float(rules["absolute_cross_market_residual_z_min"]),
            "RESIDUAL_SHARE_TOO_SMALL": float(event["residual_share"])
            >= float(rules["minimum_residual_share_of_focal_move"]),
            "PRE_VOLUME_WEAK": float(event["pre_volume_z"])
            >= float(rules["pre_expiry_volume_z_min"]),
            "PRE_OI_BUILDUP_WEAK": float(event["pre_oi_change_z"])
            >= float(rules["pre_expiry_oi_buildup_z_min"]),
            "PRE_TAKER_NOT_ALIGNED": pre_direction
            * float(event["pre_taker_pressure"])
            >= float(rules["directional_pre_expiry_taker_pressure_min"]),
            "DELIVERY_RETURN_NOT_ALIGNED": pre_direction
            * float(event["delivery_return"])
            > float(rules["directional_delivery_return_min"]),
            "DELIVERY_TAKER_NOT_ALIGNED": pre_direction
            * float(event["delivery_taker_pressure"])
            >= float(rules["directional_delivery_taker_pressure_min"]),
            "NO_POST_EXPIRY_RETURN_REVERSAL": pre_direction
            * float(event["confirmation_return"])
            < float(rules["post_expiry_reversal_return_max"]),
            "NO_POST_EXPIRY_FLOW_REVERSAL": pre_direction
            * float(event["confirmation_taker_pressure"])
            < float(rules["post_expiry_reversal_taker_pressure_max"]),
            "POST_EXPIRY_OLD_DIRECTION_OI_PERSISTS": float(
                event["confirmation_oi_change_z"]
            )
            <= float(rules["post_expiry_oi_change_z_max"]),
        }
        failed = [reason for reason, passed in checks.items() if not passed]
        if failed:
            rejected.update(failed)
            continue
        direction = -pre_direction
        entry = float(event["entry_price"])
        target = float(event["equilibrium_target"])
        stop = (
            float(event["combined_high"])
            if direction < 0
            else float(event["combined_low"])
        )
        valid = stop < entry < target if direction > 0 else target < entry < stop
        if not valid:
            rejected["INVALID_POST_EXPIRY_GEOMETRY"] += 1
            continue
        loss = abs(entry - stop) / entry + cost_return
        reward = abs(target - entry) / entry - cost_return
        structural_r = reward / max(loss, 1e-12)
        if structural_r < float(rules["minimum_net_structural_r"]):
            rejected["INSUFFICIENT_NET_STRUCTURAL_R"] += 1
            continue
        outcome = first_passage(
            prices[str(event["symbol"])],
            event,
            direction=direction,
            stop=stop,
            target=target,
        )
        if outcome is None:
            rejected["INCOMPLETE_OUTCOME_PATH"] += 1
            continue
        record = event.to_dict()
        record.update(
            {
                "route": ROUTE,
                "direction_sign": direction,
                "direction": "LONG" if direction > 0 else "SHORT",
                "stop_price": stop,
                "target_price": target,
                "loss_fraction_with_cost": loss,
                "reward_fraction_after_cost": reward,
                "net_structural_r": structural_r,
                "rank_score": abs(float(event["residual_z"]))
                * max(float(event["pre_oi_change_z"]), 0.0)
                * structural_r,
                **outcome,
                "net_return": float(outcome["gross_return"]) - cost_return,
            }
        )
        records.append(record)
    return pd.DataFrame(records), rejected


def arbitrate(candidates: pd.DataFrame) -> tuple[pd.DataFrame, Counter[str]]:
    if candidates.empty:
        return candidates.copy(), Counter()
    ordered = candidates.sort_values(
        ["expiry_ts", "rank_score", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    for _, group in ordered.groupby("expiry_ts", sort=True):
        selected.append(group.iloc[0])
        skips["SAME_EXPIRY_LOSER"] += max(0, len(group.index) - 1)
    return pd.DataFrame(selected).sort_values("entry_ts", kind="stable").reset_index(drop=True), skips


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
            "win_rate": None,
            "payoff_ratio": None,
            "net_t_stat": None,
            "mean_net_structural_r": None,
            "positive_months": 0,
            "active_months": 0,
            "positive_month_share": 0.0,
            "symbol_counts": {},
            "exit_reason_counts": {},
            "same_bar_ambiguities": 0,
        }
    net = sample["net_return"]
    monthly = (
        sample.set_index("entry_ts")["net_return"].resample("MS").sum().dropna()
    )
    return {
        "start": start,
        "end_exclusive": end,
        "calendar_days": days,
        "trades": len(sample.index),
        "trades_per_day": len(sample.index) / max(days, 1),
        "mean_gross_bps": float(sample["gross_return"].mean() * 10_000.0),
        "mean_net_bps": float(net.mean() * 10_000.0),
        "win_rate": float((net > 0.0).mean()),
        "payoff_ratio": payoff(net),
        "net_t_stat": t_stat(net),
        "mean_net_structural_r": float(sample["net_structural_r"].mean()),
        "positive_months": int((monthly > 0.0).sum()),
        "active_months": len(monthly.index),
        "positive_month_share": float((monthly > 0.0).mean()),
        "symbol_counts": dict(Counter(sample["symbol"].astype(str))),
        "exit_reason_counts": dict(Counter(sample["exit_reason"].astype(str))),
        "same_bar_ambiguities": int(sample["same_bar_ambiguous"].sum()),
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v27-expiry-hedge-release-v1":
        raise RuntimeError("unexpected Candidate 15 V27 protocol")
    data = protocol["data"]
    evaluation = protocol["evaluation"]
    start = date.fromisoformat(data["start"])
    end = date.fromisoformat(data["end_exclusive"])
    output.mkdir(parents=True, exist_ok=True)
    data_dir = output / "data"

    prices: dict[str, pd.DataFrame] = {}
    price_manifest: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {
            pool.submit(load_five_minute, symbol, protocol, data_dir): symbol
            for symbol in CONTEXT_SYMBOLS
        }
        for job in as_completed(jobs):
            symbol = jobs[job]
            frame, records = job.result()
            prices[symbol] = frame
            price_manifest.extend(records)

    metric_tasks: list[tuple[str, str, Path]] = []
    for symbol in TRADE_SYMBOLS:
        for day in oi_base.days(start, end):
            token = day.isoformat()
            metric_tasks.append(
                (
                    symbol,
                    token,
                    data_dir
                    / "metrics"
                    / symbol
                    / f"{symbol}-metrics-{token}.zip",
                )
            )
    metric_manifest: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=24) as pool:
        jobs = [pool.submit(oi_base.download_metric, task) for task in metric_tasks]
        for job in as_completed(jobs):
            metric_manifest.append(job.result())
    metrics: dict[str, pd.DataFrame] = {}
    for symbol in TRADE_SYMBOLS:
        metrics[symbol] = metrics_adapter.load_metrics(
            sorted((data_dir / "metrics" / symbol).glob("*.zip")),
            start,
            end,
        )
    write_json(
        output / "data_manifest.json",
        {
            "schema": "candidate-15-v27-data-manifest-v1",
            "protocol": protocol["schema"],
            "price_files": sorted(
                price_manifest,
                key=lambda item: (item.get("symbol", ""), item.get("token", "")),
            ),
            "metric_files": sorted(
                metric_manifest,
                key=lambda item: (item.get("symbol", ""), item.get("date", "")),
            ),
        },
    )

    raw = daily_raw_events(prices, metrics, protocol)
    featured = add_prior_state(raw, protocol)
    candidates, rejections = classify(featured, prices, protocol)
    selected, arbitration = arbitrate(candidates)
    raw.to_csv(output / "raw_daily_expiry_events.csv", index=False)
    featured.to_csv(output / "featured_daily_expiry_events.csv", index=False)
    candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)

    summaries = {
        name: summarize(
            selected,
            evaluation[f"{name}_start"],
            evaluation[f"{name}_end_exclusive"],
        )
        for name in (
            "development",
            "stability",
            "july_confirmation",
            "latest_pulse",
        )
    }
    development = summaries["development"]
    stability = summaries["stability"]
    july = summaries["july_confirmation"]
    pulse = summaries["latest_pulse"]
    gate = protocol["advance_gate"]
    max_symbol_share = (
        max(stability["symbol_counts"].values()) / max(stability["trades"], 1)
        if stability["symbol_counts"]
        else 1.0
    )
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
        "symbol_concentration": max_symbol_share <= float(gate["maximum_single_symbol_share"]),
    }
    advance = all(checks.values())
    classification = (
        "V27_EXPIRY_HEDGE_RELEASE_ADVANCE_TO_NAUTILUS"
        if advance
        else "V27_EXPIRY_HEDGE_RELEASE_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        "Freeze the exact expiry family and implement NautilusTrader bracket execution before integration."
        if advance
        else "Do not tune the expiry family after these declared results; move to another independent mechanism."
    )
    summary = {
        "schema": "candidate-15-v27-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "raw_daily_events": len(raw.index),
        "featured_daily_events": len(featured.index),
        "executable_candidates": len(candidates.index),
        "selected_trades": len(selected.index),
        "logic_rejections": dict(rejections),
        "arbitration_skips": dict(arbitration),
        "development": development,
        "stability": stability,
        "july_confirmation": july,
        "latest_pulse": pulse,
        "maximum_stability_symbol_share": max_symbol_share,
        "advance_checks": checks,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V27 — Daily option-expiry hedge-release diagnostic",
        "",
        f"**{classification}**",
        "",
        "The fixed 08:00 UTC context combines an abnormal four-hour focal/residual move, elevated futures OI and delivery-window pressure. A wholly subsequent 08:00-08:15 price/flow reversal starts the tradable leg. Binance OI is treated only as a positioning proxy, not option gamma.",
        "",
    ]
    for title, record in (
        ("Development", development),
        ("Year-long stability", stability),
        ("July 2026 confirmation", july),
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
                f"- mean net structural R: `{record['mean_net_structural_r']}`",
                f"- positive months: `{record['positive_months']} / {record['active_months']}`",
                f"- symbol counts: `{record['symbol_counts']}`",
                f"- exit reasons: `{record['exit_reason_counts']}`",
                f"- same-bar conservative stops: `{record['same_bar_ambiguities']}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Advance checks",
            *[f"- {key}: `{value}`" for key, value in checks.items()],
            "",
            "## Logic rejections",
            f"`{dict(rejections)}`",
            "",
            "## Decision",
            decision,
            "",
            "This is not a success or synthetic NAV claim. A pass still requires frozen NautilusTrader orders, exact current-NAV 3% risk sizing, all costs, one global slot and continuous-account validation.",
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
