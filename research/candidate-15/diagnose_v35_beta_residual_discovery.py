#!/usr/bin/env python3
"""Candidate 15 V35 prior-only beta-residual discovery continuation screen."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import diagnose_v28_adaptive_6h_trend as common

SYMBOLS = common.SYMBOLS
ROUTE = "BETA_RESIDUAL_PRICE_DISCOVERY_CONTINUATION"


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


def resample_thirty(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.resample(
        "30min",
        closed="right",
        label="right",
        origin="start_day",
    )
    output = pd.DataFrame(index=grouped.size().index)
    output["count"] = grouped.size()
    output["open"] = grouped["open"].first()
    output["high"] = grouped["high"].max()
    output["low"] = grouped["low"].min()
    output["close"] = grouped["close"].last()
    output["quote_volume"] = grouped["quote_volume"].sum()
    output["taker_buy_quote_volume"] = grouped[
        "taker_buy_quote_volume"
    ].sum()
    return output[output["count"] == 6].copy()


def prior_z(
    value: pd.Series,
    lookback: int,
    minimum: int,
) -> pd.Series:
    prior = value.shift(1)
    mean = prior.rolling(lookback, min_periods=minimum).mean()
    standard = (
        prior.rolling(lookback, min_periods=minimum)
        .std(ddof=0)
        .replace(0.0, np.nan)
    )
    return (value - mean) / standard


def base_frames(
    five_frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for symbol, five in five_frames.items():
        frame = resample_thirty(five)
        close = frame["close"].astype(float)
        previous = close.shift(1)
        frame["log_return"] = np.log(
            close / previous.replace(0.0, np.nan)
        )
        true_range = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - previous).abs(),
                (frame["low"] - previous).abs(),
            ],
            axis=1,
        ).max(axis=1)
        frame["atr_30m"] = true_range.rolling(
            int(rules["atr_lookback_bars"]),
            min_periods=int(rules["atr_lookback_bars"]),
        ).mean()
        frame["body_fraction"] = (
            (frame["close"] - frame["open"]).abs()
            / (frame["high"] - frame["low"]).replace(0.0, np.nan)
        )
        frame["body_direction"] = np.sign(
            frame["close"] - frame["open"]
        )
        frame["taker_pressure"] = (
            2.0
            * frame["taker_buy_quote_volume"]
            / frame["quote_volume"].replace(0.0, np.nan)
            - 1.0
        ).clip(-1.0, 1.0)
        volume_median = frame["quote_volume"].shift(1).rolling(
            int(rules["volume_median_lookback_bars"]),
            min_periods=int(rules["beta_minimum_prior_bars"]),
        ).median()
        frame["volume_ratio"] = (
            frame["quote_volume"]
            / volume_median.replace(0.0, np.nan)
        )
        frame["symbol"] = symbol
        frame["event_ts"] = frame.index
        frames[symbol] = frame.replace([np.inf, -np.inf], np.nan)
    return frames


def add_beta_residuals(
    frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> dict[str, pd.DataFrame]:
    returns = pd.DataFrame(
        {symbol: frame["log_return"] for symbol, frame in frames.items()}
    )
    cumulative_z: dict[str, pd.Series] = {}
    enriched_frames: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        peers = [name for name in SYMBOLS if name != symbol]
        factor = returns[peers].median(axis=1)
        focal = returns[symbol]
        prior_product = (focal * factor).shift(1)
        prior_factor_sq = (factor * factor).shift(1)
        numerator = prior_product.rolling(
            int(rules["beta_lookback_bars"]),
            min_periods=int(rules["beta_minimum_prior_bars"]),
        ).sum()
        denominator = prior_factor_sq.rolling(
            int(rules["beta_lookback_bars"]),
            min_periods=int(rules["beta_minimum_prior_bars"]),
        ).sum().replace(0.0, np.nan)
        beta = numerator / denominator
        residual_return = focal - beta * factor
        residual_sum = residual_return.rolling(
            int(rules["residual_horizon_bars"]),
            min_periods=int(rules["residual_horizon_bars"]),
        ).sum()
        residual_z = prior_z(
            residual_sum,
            int(rules["residual_z_lookback_bars"]),
            int(rules["residual_z_minimum_prior_bars"]),
        )
        atr_fraction = frame["atr_30m"] / frame["close"].replace(
            0.0,
            np.nan,
        )
        enriched = frame.copy()
        enriched["peer_factor_return"] = factor
        enriched["prior_beta"] = beta
        enriched["residual_return"] = residual_return
        enriched["residual_return_atr"] = (
            residual_return / atr_fraction.replace(0.0, np.nan)
        )
        enriched["residual_horizon_sum"] = residual_sum
        enriched["residual_horizon_z"] = residual_z
        enriched_frames[symbol] = enriched.replace(
            [np.inf, -np.inf],
            np.nan,
        )
        cumulative_z[symbol] = residual_z

    z_table = pd.DataFrame(cumulative_z)
    absolute = z_table.abs()
    top = absolute.max(axis=1)
    second = absolute.apply(
        lambda row: row.nlargest(2).iloc[-1]
        if row.notna().sum() >= 2
        else np.nan,
        axis=1,
    )
    for symbol, frame in enriched_frames.items():
        frame["is_absolute_residual_leader"] = (
            absolute[symbol] >= top - 1e-12
        )
        frame["residual_leader_gap_z"] = top - second
        frame["direction_value"] = np.sign(
            frame["residual_horizon_z"]
        )
    return enriched_frames


def confirm_and_simulate(
    item: pd.Series,
    five: pd.DataFrame,
    thirty: pd.DataFrame,
    rules: dict[str, Any],
) -> dict[str, Any] | None:
    event_ts = pd.Timestamp(item["event_ts"])
    direction = float(item["direction_value"])
    confirmation_end = event_ts + pd.Timedelta(
        minutes=int(rules["confirmation_minutes"])
    )
    confirmation = five[
        (five.index > event_ts) & (five.index <= confirmation_end)
    ]
    if len(confirmation.index) != int(rules["confirmation_minutes"]) // 5:
        return None
    opening = float(confirmation.iloc[0]["open"])
    closing = float(confirmation.iloc[-1]["close"])
    high = float(confirmation["high"].max())
    low = float(confirmation["low"].min())
    confirmation_return = closing / opening - 1.0
    body_fraction = abs(closing - opening) / max(high - low, 1e-12)
    pressure = (
        2.0
        * float(confirmation["taker_buy_quote_volume"].sum())
        / max(float(confirmation["quote_volume"].sum()), 1e-12)
        - 1.0
    )
    if direction * confirmation_return <= 0.0:
        return None
    if body_fraction < float(rules["minimum_confirmation_body_fraction"]):
        return None
    if (
        direction * pressure
        < float(rules["minimum_directional_taker_pressure"])
    ):
        return None

    entry = closing
    atr = float(item["atr_30m"])
    buffer = float(rules["initial_stop_atr_buffer"]) * atr
    if direction > 0.0:
        initial_stop = float(item["low"]) - buffer
    else:
        initial_stop = float(item["high"]) + buffer
    if direction * (entry - initial_stop) <= 0.0:
        return None

    cap = confirmation_end + pd.Timedelta(
        minutes=int(rules["maximum_hold_minutes"])
    )
    path = five[(five.index > confirmation_end) & (five.index <= cap)]
    if path.empty:
        return None

    available_atr = thirty["atr_30m"]
    stop = initial_stop
    best_close = entry
    exit_ts: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason = ""
    updates = 0
    for timestamp, bar in path.iterrows():
        if direction > 0.0 and float(bar["low"]) <= stop:
            exit_ts = pd.Timestamp(timestamp)
            exit_price = stop
            exit_reason = "TRAILING_STOP"
            break
        if direction < 0.0 and float(bar["high"]) >= stop:
            exit_ts = pd.Timestamp(timestamp)
            exit_price = stop
            exit_reason = "TRAILING_STOP"
            break

        if timestamp.minute in (0, 30):
            bar_close = float(bar["close"])
            best_close = (
                max(best_close, bar_close)
                if direction > 0.0
                else min(best_close, bar_close)
            )
            current_atr = available_atr.get(timestamp, np.nan)
            if pd.isna(current_atr):
                current_atr = atr
            if direction > 0.0:
                new_stop = max(
                    stop,
                    best_close
                    - float(rules["trailing_stop_atr"])
                    * float(current_atr),
                )
            else:
                new_stop = min(
                    stop,
                    best_close
                    + float(rules["trailing_stop_atr"])
                    * float(current_atr),
                )
            if abs(new_stop - stop) > 1e-12:
                stop = new_stop
                updates += 1

    if exit_ts is None:
        exit_ts = pd.Timestamp(path.index[-1])
        exit_price = float(path.iloc[-1]["close"])
        exit_reason = "SIX_HOUR_CAP"

    gross = direction * (float(exit_price) / entry - 1.0)
    cost = (
        float(rules["execution_round_trip_cost_bps"])
        + float(rules["funding_and_unmodeled_impact_reserve_bps"])
    ) / 10_000.0
    quality = (
        abs(float(item["residual_horizon_z"]))
        * abs(float(item["residual_return_atr"]))
        * max(float(item["volume_ratio"]), 0.0)
    )
    return {
        "event_ts": event_ts,
        "entry_ts": confirmation_end,
        "exit_ts": exit_ts,
        "symbol": str(item["symbol"]),
        "route": ROUTE,
        "direction": "LONG" if direction > 0.0 else "SHORT",
        "direction_value": direction,
        "prior_beta": float(item["prior_beta"]),
        "peer_factor_return": float(item["peer_factor_return"]),
        "residual_return": float(item["residual_return"]),
        "residual_return_atr": float(item["residual_return_atr"]),
        "residual_horizon_sum": float(item["residual_horizon_sum"]),
        "residual_horizon_z": float(item["residual_horizon_z"]),
        "residual_leader_gap_z": float(item["residual_leader_gap_z"]),
        "volume_ratio": float(item["volume_ratio"]),
        "event_body_fraction": float(item["body_fraction"]),
        "event_taker_pressure": float(item["taker_pressure"]),
        "confirmation_return": confirmation_return,
        "confirmation_body_fraction": body_fraction,
        "confirmation_taker_pressure": pressure,
        "entry_price": entry,
        "initial_stop": initial_stop,
        "final_stop": stop,
        "exit_price": float(exit_price),
        "exit_reason": exit_reason,
        "trailing_updates": updates,
        "holding_minutes": (
            exit_ts - confirmation_end
        ).total_seconds() / 60.0,
        "rank_score": quality,
        "gross_return": gross,
        "net_return": gross - cost,
    }


def candidates(
    frames: dict[str, pd.DataFrame],
    five_frames: dict[str, pd.DataFrame],
    rules: dict[str, Any],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for symbol, frame in frames.items():
        eligible = frame[
            (
                frame["residual_horizon_z"].abs()
                >= float(rules["absolute_residual_z_min"])
            )
            & (
                frame["residual_leader_gap_z"]
                >= float(rules["residual_leader_gap_z_min"])
            )
            & frame["is_absolute_residual_leader"].fillna(False)
            & (
                frame["residual_return_atr"].abs()
                >= float(rules["event_residual_atr_min"])
            )
            & (
                np.sign(frame["residual_return"])
                == frame["direction_value"]
            )
            & (
                frame["body_direction"]
                == frame["direction_value"]
            )
            & (
                frame["body_fraction"]
                >= float(rules["minimum_body_fraction"])
            )
            & (
                frame["volume_ratio"]
                >= float(rules["minimum_volume_ratio"])
            )
            & (
                frame["direction_value"] * frame["taker_pressure"]
                >= float(rules["minimum_directional_taker_pressure"])
            )
        ]
        for _, item in eligible.iterrows():
            result = confirm_and_simulate(
                item,
                five_frames[symbol],
                frame,
                rules,
            )
            if result is not None:
                rows.append(result)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values(
        ["entry_ts", "rank_score", "symbol"],
        ascending=[True, False, True],
        kind="stable",
    )


def arbitrate(
    frame: pd.DataFrame,
    rules: dict[str, Any],
) -> tuple[pd.DataFrame, Counter[str]]:
    if frame.empty:
        return frame.copy(), Counter()
    selected: list[pd.Series] = []
    skips: Counter[str] = Counter()
    free_at = pd.Timestamp.min.tz_localize("UTC")
    last_symbol_entry: dict[str, pd.Timestamp] = {}
    cooldown = pd.Timedelta(
        minutes=int(rules["same_symbol_cooldown_minutes"])
    )
    for entry_ts, group in frame.groupby("entry_ts", sort=True):
        winner = group.iloc[0]
        skips["SAME_EVENT_LOSER"] += max(0, len(group.index) - 1)
        timestamp = pd.Timestamp(entry_ts)
        symbol = str(winner["symbol"])
        if timestamp < free_at:
            skips["GLOBAL_POSITION_OCCUPIED"] += 1
            continue
        if (
            symbol in last_symbol_entry
            and timestamp < last_symbol_entry[symbol] + cooldown
        ):
            skips["SAME_SYMBOL_COOLDOWN"] += 1
            continue
        selected.append(winner)
        free_at = pd.Timestamp(winner["exit_ts"])
        last_symbol_entry[symbol] = timestamp
    if not selected:
        return frame.iloc[0:0].copy(), skips
    output = pd.DataFrame(selected).reset_index(drop=True)
    if len(output.index) > 1:
        opened = pd.to_datetime(output["entry_ts"], utc=True)
        closed = pd.to_datetime(output["exit_ts"], utc=True)
        if not (
            opened.iloc[1:].reset_index(drop=True)
            >= closed.iloc[:-1].reset_index(drop=True)
        ).all():
            raise RuntimeError("global overlap survived V35 arbitration")
    return output, skips


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
            "mean_holding_minutes": None,
            "symbol_counts": {},
            "exit_reasons": {},
        }
    sample["month"] = sample["entry_ts"].dt.to_period("M").astype(str)
    monthly = sample.groupby("month")["net_return"].sum()
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
        "mean_holding_minutes": float(sample["holding_minutes"].mean()),
        "symbol_counts": {
            str(key): int(value)
            for key, value in sample["symbol"].value_counts().items()
        },
        "exit_reasons": {
            str(key): int(value)
            for key, value in sample["exit_reason"].value_counts().items()
        },
    }


def execute(protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = load_object(protocol_path)
    if protocol["schema"] != "candidate-15-v35-beta-residual-discovery-v1":
        raise RuntimeError("unexpected V35 protocol")
    output.mkdir(parents=True, exist_ok=True)
    paths, manifest = common.download_data(protocol, output)
    write_json(
        output / "data_manifest.json",
        {
            "schema": "candidate-15-v35-data-manifest-v1",
            "files": manifest,
        },
    )
    start = date.fromisoformat(protocol["data"]["start"])
    end = date.fromisoformat(protocol["data"]["end_exclusive"])
    five_frames = {
        symbol: common.load_symbol(paths[symbol], start, end)
        for symbol in SYMBOLS
    }
    frames = base_frames(five_frames, protocol["fixed_rules"])
    frames = add_beta_residuals(frames, protocol["fixed_rules"])
    all_candidates = candidates(
        frames,
        five_frames,
        protocol["fixed_rules"],
    )
    selected, skips = arbitrate(
        all_candidates,
        protocol["fixed_rules"],
    )
    all_candidates.to_csv(output / "all_executable_candidates.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)

    evaluation = protocol["evaluation"]
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
    total = sum(stability["symbol_counts"].values())
    max_symbol_share = (
        max(stability["symbol_counts"].values(), default=0)
        / max(total, 1)
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
        "symbol_concentration": (
            max_symbol_share <= float(gate["maximum_single_symbol_share"])
        ),
    }
    positive_all = all(
        summaries[name]["mean_net_bps"] is not None
        and summaries[name]["mean_net_bps"] > 0.0
        for name in summaries
    )
    advance = all(checks.values()) and positive_all
    classification = (
        "V35_BETA_RESIDUAL_DISCOVERY_ADVANCE_TO_NAUTILUS"
        if advance
        else "V35_BETA_RESIDUAL_DISCOVERY_REJECTED_OR_UNDERPOWERED"
    )
    decision = (
        "The fixed beta-residual discovery continuation survived every split; "
        "freeze and implement it in NautilusTrader."
        if advance
        else "The fixed beta-residual discovery continuation did not survive "
        "every split. Do not tune beta, horizon, z-score, confirmation or stops."
    )
    summary = {
        "schema": "candidate-15-v35-summary-v1",
        "classification": classification,
        "advance_to_nautilus": advance,
        "executable_candidates": len(all_candidates.index),
        "selected_trades": len(selected.index),
        "arbitration_skips": dict(skips),
        **summaries,
        "advance_checks": checks,
        "positive_across_all_declared_splits": positive_all,
        "maximum_stability_symbol_share": max_symbol_share,
        "decision": decision,
    }
    write_json(output / "summary.json", summary)

    lines = [
        "# Candidate 15 V35 — Beta-residual price discovery",
        "",
        f"**{classification}**",
        "",
        "The common crypto factor is removed with prior-only rolling beta. "
        "Only the unique 24-hour residual leader with high notional and aligned "
        "flow may enter after a separate fifteen-minute confirmation.",
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
                f"- trades / day: `{record['trades']} / "
                f"{record['trades_per_day']}`",
                f"- gross / net mean: `{record['mean_gross_bps']} / "
                f"{record['mean_net_bps']}` bp",
                f"- win rate / payoff: `{record['win_rate']} / "
                f"{record['payoff_ratio']}`",
                f"- net t-stat: `{record['net_t_stat']}`",
                f"- positive months: `{record['positive_months']} / "
                f"{record['active_months']}`",
                f"- mean holding minutes: `{record['mean_holding_minutes']}`",
                f"- symbol counts: `{record['symbol_counts']}`",
                f"- exit reasons: `{record['exit_reasons']}`",
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
            "This is an economic mechanism screen. A pass still requires "
            "frozen NautilusTrader execution, current-NAV 3% sizing, one "
            "global slot and continuous-account validation.",
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
