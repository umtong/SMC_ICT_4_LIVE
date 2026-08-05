#!/usr/bin/env python3
"""Diagnose completed-range auction regimes against actual causal trade paths.

The production candidate treats every completed four-hour range as an equal
auction context.  The 2024 evaluation disproves that assumption.  This module
computes only information available when a block completes and asks whether the
block was rotational (inventory transfer around value) or directional
(information/liquidity shock that relocated value).

It uses the exact failed-auction state machine and one-bar execution delay from
``portfolio_probe``.  No feature from the future path is fed back into signal
generation; outcome columns exist only for diagnosis.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
for item in (HERE, SRC):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from core import AuctionStateMachine, CandidateConfig  # noqa: E402
from data import load_interval, parse_utc_date, to_auction_bars  # noqa: E402
from portfolio_probe import Variant, simulate  # noqa: E402


NS_PER_MINUTE = 60_000_000_000


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime, str]]:
    def week(label: str, value: str, role: str) -> tuple[str, datetime, datetime, str]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7), role

    long_start = parse_utc_date(str(research["long_start"]))
    long_end = parse_utc_date(str(research["long_end"]))
    return [
        week("discovery", str(research["discovery_week"]), "quick"),
        *[
            week(f"confirmation-{index + 1}", value, "quick")
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        ("long-evaluation", long_start, long_end, "development"),
    ]


def _block_features(frame: pd.DataFrame, range_minutes: int) -> pd.DataFrame:
    range_ns = range_minutes * NS_PER_MINUTE
    values = frame.copy()
    values["ts_ns"] = values["close_dt"].astype("int64")
    values["block_id"] = values["ts_ns"] // range_ns
    values["signed_flow"] = 2.0 * values["taker_buy_quote_volume"] - values["quote_volume"]
    values["close_change"] = values["close"].diff().abs()
    # Do not carry path length across block boundaries.
    values.loc[values["block_id"].ne(values["block_id"].shift()), "close_change"] = (
        values.loc[values["block_id"].ne(values["block_id"].shift()), "close"]
        - values.loc[values["block_id"].ne(values["block_id"].shift()), "open"]
    ).abs()

    rows: list[dict[str, Any]] = []
    for block_id, group in values.groupby("block_id", sort=True):
        group = group.sort_values("ts_ns", kind="stable")
        open_price = float(group.iloc[0]["open"])
        close_price = float(group.iloc[-1]["close"])
        high = float(group["high"].max())
        low = float(group["low"].min())
        width = high - low
        path_length = float(group["close_change"].sum())
        quote_volume = float(group["quote_volume"].sum())
        base_volume = float(group["base_volume"].sum())
        signed_flow = float(group["signed_flow"].sum())
        vwap = quote_volume / base_volume if base_volume > 0.0 else close_price
        midpoint = 0.5 * (high + low)
        closes = group["close"].astype(float)
        close_location = (close_price - low) / width if width > 0.0 else 0.5
        body = close_price - open_price
        body_abs = abs(body)
        path_efficiency = body_abs / path_length if path_length > 0.0 else 0.0
        range_efficiency = width / path_length if path_length > 0.0 else 0.0
        flow_imbalance = signed_flow / quote_volume if quote_volume > 0.0 else 0.0
        price_direction = 1.0 if body > 0.0 else -1.0 if body < 0.0 else 0.0
        flow_alignment = flow_imbalance * price_direction
        upper_quartile = float((closes >= low + 0.75 * width).mean()) if width > 0.0 else 0.0
        lower_quartile = float((closes <= low + 0.25 * width).mean()) if width > 0.0 else 0.0
        centered = closes - midpoint
        signs = np.sign(centered.to_numpy())
        nonzero = signs[signs != 0]
        mid_crosses = int(np.sum(nonzero[1:] != nonzero[:-1])) if len(nonzero) > 1 else 0
        minute_returns = group["close"].astype(float).pct_change().dropna()
        signed_return_flow = (
            float(np.corrcoef(minute_returns, group["signed_flow"].iloc[1:])[0, 1])
            if len(minute_returns) >= 20
            and float(minute_returns.std()) > 0.0
            and float(group["signed_flow"].iloc[1:].std()) > 0.0
            else 0.0
        )
        rows.append(
            {
                "block_id": int(block_id),
                "start_ns": int(group.iloc[0]["ts_ns"] - NS_PER_MINUTE + 1),
                "end_ns": int((int(block_id) + 1) * range_ns),
                "bars": int(len(group)),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close_price,
                "midpoint": midpoint,
                "width": width,
                "body": body,
                "body_abs": body_abs,
                "close_location": close_location,
                "path_length": path_length,
                "path_efficiency": path_efficiency,
                "range_efficiency": range_efficiency,
                "quote_volume": quote_volume,
                "signed_flow": signed_flow,
                "flow_imbalance": flow_imbalance,
                "flow_alignment": flow_alignment,
                "vwap": vwap,
                "close_minus_vwap_fraction": (close_price - vwap) / width if width > 0.0 else 0.0,
                "upper_quartile_fraction": upper_quartile,
                "lower_quartile_fraction": lower_quartile,
                "extreme_residence_fraction": upper_quartile + lower_quartile,
                "mid_crosses": mid_crosses,
                "signed_return_flow_corr": signed_return_flow,
            },
        )
    result = pd.DataFrame(rows).sort_values("block_id", kind="stable").reset_index(drop=True)
    for lag in (1, 2, 3, 6, 12):
        result[f"mid_delta_{lag}"] = result["midpoint"] - result["midpoint"].shift(lag)
        result[f"width_ratio_{lag}"] = result["width"] / result["width"].shift(lag)
    result["prior_mid_vol_6"] = result["midpoint"].diff().rolling(6).std()
    result["prior_direction_consistency_6"] = (
        result["midpoint"].diff().apply(np.sign).rolling(6).mean().abs()
    )
    return result


def _event_features(bars: list[Any], candidate: CandidateConfig) -> pd.DataFrame:
    machine = AuctionStateMachine(candidate, instrument_id="BTCUSDT-PERP.BINANCE")
    for item in bars:
        machine.on_bar(item)
    rows: dict[str, dict[str, Any]] = {}
    for event in machine.transitions:
        row = rows.setdefault(event.scenario_id, {"scenario_id": event.scenario_id})
        if event.event_type == "LIQUIDITY_PROBE_REJECTED":
            details = event.details
            row.update(
                {
                    "probe_time_ns": event.event_time_ns,
                    "probe_flow_z": details.get("flow_z"),
                    "probe_volume_z": details.get("volume_z"),
                    "probe_atr": details.get("atr"),
                    "probe_boundary": details.get("boundary"),
                    "probe_excursion_extreme": details.get("excursion_extreme"),
                    "probe_internal_break": details.get("internal_break"),
                },
            )
        elif event.event_type == "REVERSAL_DISPLACEMENT_CONFIRMED":
            details = event.details
            row.update(
                {
                    "displacement_time_ns": event.event_time_ns,
                    "displacement_flow_z": details.get("flow_z"),
                    "displacement_body_atr": details.get("body_atr"),
                    "structure_overshoot_atr": details.get("structure_overshoot_atr"),
                    "displacement_atr": details.get("atr"),
                },
            )
    return pd.DataFrame(rows.values())


def _score_rules(frame: pd.DataFrame, role: str) -> list[dict[str, Any]]:
    source = frame.loc[frame["role"] == role].copy()
    if source.empty:
        return []
    rules: dict[str, pd.Series] = {
        "all": pd.Series(True, index=source.index),
        "rotational-core": (
            (source["path_efficiency"] <= 0.20)
            & (source["flow_imbalance"].abs() <= 0.12)
            & source["close_location"].between(0.20, 0.80)
            & (source["mid_crosses"] >= 3)
        ),
        "rotational-or-strong-reversal-flow": (
            (
                (source["path_efficiency"] <= 0.20)
                & (source["flow_imbalance"].abs() <= 0.12)
                & source["close_location"].between(0.20, 0.80)
                & (source["mid_crosses"] >= 3)
            )
            | (source["trade_direction_flow_z"] >= 1.75)
        ),
        "low-efficiency": source["path_efficiency"] <= 0.20,
        "balanced-flow": source["flow_imbalance"].abs() <= 0.12,
        "central-close": source["close_location"].between(0.20, 0.80),
        "strong-reversal-flow": source["trade_direction_flow_z"] >= 1.75,
    }
    rows: list[dict[str, Any]] = []
    for name, mask in rules.items():
        values = source.loc[mask, "realized_r"].dropna()
        gross_profit = float(values[values > 0.0].sum())
        gross_loss = abs(float(values[values < 0.0].sum()))
        rows.append(
            {
                "role": role,
                "rule": name,
                "trades": int(len(values)),
                "mean_r": float(values.mean()) if len(values) else None,
                "sum_r": float(values.sum()) if len(values) else 0.0,
                "win_rate": float((values > 0.0).mean()) if len(values) else None,
                "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
            },
        )
    return rows


def run(args: argparse.Namespace) -> int:
    raw = json.loads(args.config.read_text(encoding="utf-8"))
    candidate = CandidateConfig.from_mapping(raw["candidate"])
    research = dict(raw["research"])
    execution = dict(raw["execution"])
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    joined_rows: list[pd.DataFrame] = []

    for label, start, end, role in _segments(research):
        frame, _ = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180),
        )
        bars = to_auction_bars(frame)
        trades, metrics, _ = simulate(
            variant=Variant("btc-240", ("BTCUSDT",), (candidate.range_minutes,)),
            bars_by_symbol={"BTCUSDT": bars},
            evaluation_start=start,
            evaluation_end=end,
            base_candidate=candidate,
            cost=float(execution["all_in_cost_bps_per_side"]) / 10_000.0,
            minimum_price_risk_fraction=float(execution["minimum_price_risk_fraction"]),
            minimum_net_reward_risk=float(execution["minimum_net_reward_risk"]),
            starting_nav=float(execution["starting_nav"]),
            risk_rates=(0.01,),
        )
        blocks = _block_features(frame, candidate.range_minutes)
        events = _event_features(bars, candidate)
        if trades.empty:
            continue
        trades = trades.merge(events, on="scenario_id", how="left", validate="one_to_one")
        range_ns = candidate.range_minutes * NS_PER_MINUTE
        trades["anchor_block_id"] = trades["signal_time_ns"].astype("int64") // range_ns - 1
        joined = trades.merge(
            blocks,
            left_on="anchor_block_id",
            right_on="block_id",
            how="left",
            validate="many_to_one",
            suffixes=("", "_anchor"),
        )
        joined.insert(0, "segment", label)
        joined.insert(1, "role", role)
        side_sign = joined["side"].map({"LONG": 1.0, "SHORT": -1.0})
        # A short trade requires negative displacement flow; align both sides so
        # larger positive values always mean stronger reversal-direction flow.
        joined["trade_direction_flow_z"] = joined["displacement_flow_z"] * side_sign
        joined["attempt_direction_flow_z"] = -joined["probe_flow_z"] * side_sign
        joined["aligned_mid_delta_6"] = joined["mid_delta_6"] * side_sign
        joined["width_atr"] = joined["width"] / joined["atr"]
        joined["body_atr"] = joined["body_abs"] / joined["atr"]
        joined["close_edge_distance"] = np.minimum(
            joined["close_location"],
            1.0 - joined["close_location"],
        )
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        blocks.to_csv(destination / "completed_ranges.csv", index=False)
        joined.to_csv(destination / "trades_with_anchor_regime.csv", index=False)
        _atomic_json(destination / "probe_metrics.json", metrics)
        joined_rows.append(joined)

    combined = pd.concat(joined_rows, ignore_index=True)
    combined.to_csv(output / "combined_trades_with_anchor_regime.csv", index=False)
    rule_rows = _score_rules(combined, "development") + _score_rules(combined, "quick")
    rule_table = pd.DataFrame(rule_rows)
    rule_table.to_csv(output / "rule_diagnostics.csv", index=False)
    summary = {
        "rows": int(len(combined)),
        "development_rows": int((combined["role"] == "development").sum()),
        "quick_rows": int((combined["role"] == "quick").sum()),
        "rules": rule_rows,
    }
    _atomic_json(output / "anchor_regime_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache" / "candidate-01-anchor-regime")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "candidate-01-anchor-regime")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
