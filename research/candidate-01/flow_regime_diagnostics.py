#!/usr/bin/env python3
"""Diagnose causal order-flow regime changes after a failed auction.

A single displacement candle can be an isolated liquidation print or the first
observable bar of a new inventory-transfer regime.  The existing candidate
checks only the displacement bar.  This diagnostic preserves the exact
failed-auction detector, structural stop/target, one-bar delayed entry, and
cost model, then measures the completed sequence from the liquidity probe to
the confirmed displacement.

The predeclared rules test three causal mechanisms rather than searching a
parameter grid:

* persistence: reversal flow and price agree across multiple completed bars;
* change point: the mean signed flow shifts from breakout to reversal;
* absorbed effort and release: strong breakout effort produces limited
  excursion, followed by efficient travel back through the range.

All measurements end at the displacement close and are therefore available
before the existing next-bar entry.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _segments(research: dict[str, Any]) -> list[tuple[str, datetime, datetime, str]]:
    def week(label: str, value: str, role: str) -> tuple[str, datetime, datetime, str]:
        start = parse_utc_date(value)
        return label, start, start + timedelta(days=7), role

    return [
        week("discovery", str(research["discovery_week"]), "quick"),
        *[
            week(f"confirmation-{index + 1}", value, "quick")
            for index, value in enumerate(research["confirmation_weeks"])
        ],
        *[
            week(f"untouched-{index + 1}", value, "untouched")
            for index, value in enumerate(research.get("additional_random_weeks", []))
        ],
        (
            "long-evaluation",
            parse_utc_date(str(research["long_start"])),
            parse_utc_date(str(research["long_end"])),
            "development",
        ),
    ]


def _event_context(bars: list[Any], candidate: CandidateConfig) -> pd.DataFrame:
    machine = AuctionStateMachine(
        candidate,
        instrument_id=f"BTCUSDT-PERP.BINANCE:{candidate.range_minutes}m",
    )
    for bar in bars:
        machine.on_bar(bar)
    rows: dict[str, dict[str, Any]] = {}
    for event in machine.transitions:
        row = rows.setdefault(event.scenario_id, {"scenario_id": event.scenario_id})
        if event.event_type == "LIQUIDITY_PROBE_REJECTED":
            row.update(
                {
                    "probe_time_ns": event.event_time_ns,
                    "probe_flow_z": event.details.get("flow_z"),
                    "probe_volume_z": event.details.get("volume_z"),
                    "probe_boundary": event.details.get("boundary"),
                    "probe_excursion_extreme": event.details.get("excursion_extreme"),
                    "probe_atr": event.details.get("atr"),
                },
            )
        elif event.event_type == "REVERSAL_DISPLACEMENT_CONFIRMED":
            row.update(
                {
                    "displacement_time_ns": event.event_time_ns,
                    "displacement_flow_z": event.details.get("flow_z"),
                    "displacement_body_atr": event.details.get("body_atr"),
                    "structure_overshoot_atr": event.details.get("structure_overshoot_atr"),
                },
            )
    return pd.DataFrame(rows.values())


def _causal_bar_state(frame: pd.DataFrame, candidate: CandidateConfig) -> pd.DataFrame:
    result = frame[
        [
            "close_dt",
            "open",
            "high",
            "low",
            "close",
            "quote_volume",
            "taker_buy_quote_volume",
        ]
    ].copy()
    result["ts_ns"] = (
        pd.to_datetime(result["close_dt"], utc=True)
        .astype("datetime64[ns, UTC]")
        .astype("int64")
    )
    result["aggressive_imbalance"] = (
        2.0 * result["taker_buy_quote_volume"] - result["quote_volume"]
    ) / result["quote_volume"].replace(0.0, np.nan)
    flow_history = result["aggressive_imbalance"].rolling(
        candidate.flow_lookback,
        min_periods=20,
    )
    result["flow_z"] = (
        result["aggressive_imbalance"] - flow_history.mean().shift(1)
    ) / flow_history.std(ddof=0).shift(1).replace(0.0, np.nan)

    previous_close = result["close"].shift(1)
    true_range = pd.concat(
        [
            result["high"] - result["low"],
            (result["high"] - previous_close).abs(),
            (result["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_history = true_range.rolling(
        candidate.atr_lookback,
        min_periods=max(20, candidate.atr_lookback // 2),
    )
    result["prior_atr"] = atr_history.mean().shift(1)
    result["body_atr"] = (result["close"] - result["open"]) / result["prior_atr"]
    result = result.sort_values("ts_ns", kind="stable").reset_index(drop=True)
    return result


def _max_positive_run(values: np.ndarray) -> int:
    best = 0
    current = 0
    for value in values:
        if bool(value):
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _sequence_features(
    trades: pd.DataFrame,
    bars: pd.DataFrame,
) -> pd.DataFrame:
    positions = {int(value): index for index, value in enumerate(bars["ts_ns"])}
    rows: list[dict[str, Any]] = []
    for trade in trades.itertuples(index=False):
        probe_index = positions.get(int(trade.probe_time_ns))
        displacement_index = positions.get(int(trade.displacement_time_ns))
        if probe_index is None or displacement_index is None:
            raise RuntimeError(f"event time missing from causal bar frame: {trade.scenario_id}")
        if displacement_index <= probe_index:
            raise RuntimeError(f"non-forward displacement sequence: {trade.scenario_id}")

        side_sign = 1.0 if trade.side == "LONG" else -1.0
        breakout_sign = -side_sign
        pre = bars.iloc[max(0, probe_index - 14) : probe_index + 1].copy()
        post = bars.iloc[probe_index + 1 : displacement_index + 1].copy()
        if post.empty:
            raise RuntimeError(f"empty reversal sequence: {trade.scenario_id}")

        pre_flow = pd.to_numeric(pre["flow_z"], errors="coerce").dropna()
        post_flow = pd.to_numeric(post["flow_z"], errors="coerce").dropna()
        if pre_flow.empty or post_flow.empty:
            raise RuntimeError(f"insufficient flow history: {trade.scenario_id}")
        trade_flow = side_sign * post_flow.to_numpy(dtype=float)
        post_body = side_sign * pd.to_numeric(post["body_atr"], errors="coerce").to_numpy(dtype=float)
        valid_body = np.isfinite(post_body)
        positive_flow = trade_flow > 0.0
        positive_body = post_body > 0.0

        probe_close = float(bars.iloc[probe_index]["close"])
        displacement_close = float(bars.iloc[displacement_index]["close"])
        probe_atr = float(trade.probe_atr)
        close_path = np.concatenate(
            [[probe_close], pd.to_numeric(post["close"], errors="raise").to_numpy(dtype=float)],
        )
        directional_path_atr = side_sign * (displacement_close - probe_close) / probe_atr
        path_length_atr = float(np.abs(np.diff(close_path)).sum() / probe_atr)
        path_efficiency = (
            max(directional_path_atr, 0.0) / path_length_atr
            if path_length_atr > 0.0
            else 0.0
        )
        excursion_atr = abs(float(trade.probe_excursion_extreme) - float(trade.probe_boundary)) / probe_atr
        breakout_flow_mean = float((breakout_sign * pre_flow).mean())
        reversal_flow_mean = float(trade_flow.mean())
        flow_regime_shift = float(
            side_sign * (post_flow.mean() - pre_flow.mean()),
        )
        joint = positive_flow & positive_body & valid_body
        absolute_flow = float(np.abs(trade_flow).sum())

        rows.append(
            {
                "scenario_id": trade.scenario_id,
                "sequence_bars": int(len(post)),
                "breakout_flow_mean_z": breakout_flow_mean,
                "breakout_flow_positive_fraction": float(
                    (breakout_sign * pre_flow.to_numpy(dtype=float) > 0.0).mean(),
                ),
                "reversal_flow_mean_z": reversal_flow_mean,
                "reversal_flow_sum_z": float(trade_flow.sum()),
                "flow_regime_shift_z": flow_regime_shift,
                "reversal_flow_positive_fraction": float(positive_flow.mean()),
                "reversal_body_positive_fraction": float(positive_body[valid_body].mean()),
                "joint_flow_price_fraction": float(joint[valid_body].mean()),
                "maximum_positive_flow_run": _max_positive_run(positive_flow),
                "reversal_body_sum_atr": float(np.nansum(post_body)),
                "reversal_path_atr": directional_path_atr,
                "reversal_path_length_atr": path_length_atr,
                "reversal_path_efficiency": path_efficiency,
                "probe_excursion_atr": excursion_atr,
                "breakout_effort_per_excursion": breakout_flow_mean / max(excursion_atr, 1e-9),
                "reversal_impact_per_abs_flow": (
                    directional_path_atr / absolute_flow if absolute_flow > 0.0 else 0.0
                ),
            },
        )
    return pd.DataFrame(rows)


def _rules(frame: pd.DataFrame) -> dict[str, pd.Series]:
    strong_flow = frame["trade_direction_displacement_flow_z"] >= 1.70
    persistent_shift = (
        (frame["sequence_bars"] >= 2)
        & (frame["flow_regime_shift_z"] >= 1.25)
        & (frame["reversal_flow_positive_fraction"] >= 0.60)
        & (frame["joint_flow_price_fraction"] >= 0.50)
    )
    absorbed_release = (
        (frame["breakout_effort_per_excursion"] >= 1.0)
        & (frame["reversal_path_atr"] >= 0.35)
        & (frame["reversal_path_efficiency"] >= 0.55)
    )
    multi_bar_confirmation = (
        (frame["maximum_positive_flow_run"] >= 2)
        & (frame["reversal_flow_mean_z"] >= 0.35)
        & (frame["reversal_path_efficiency"] >= 0.50)
    )
    shift_and_displacement = (
        (frame["flow_regime_shift_z"] >= 1.0)
        & (frame["trade_direction_displacement_flow_z"] >= 0.75)
        & (frame["displacement_body_atr"] >= 0.50)
    )
    return {
        "all": pd.Series(True, index=frame.index),
        "strong-flow-170": strong_flow,
        "persistent-regime-shift": persistent_shift,
        "absorbed-breakout-release": absorbed_release,
        "multi-bar-confirmation": multi_bar_confirmation,
        "shift-and-displacement": shift_and_displacement,
        "state-change-or-strong-flow": strong_flow | persistent_shift | absorbed_release,
    }


def _score(frame: pd.DataFrame, role: str) -> list[dict[str, Any]]:
    source = frame.loc[frame["role"] == role].copy()
    rows: list[dict[str, Any]] = []
    for name, mask in _rules(source).items():
        selected = source.loc[mask.fillna(False)].copy()
        values = pd.to_numeric(selected["realized_r"], errors="coerce").dropna()
        gross_profit = float(values[values > 0.0].sum())
        gross_loss = abs(float(values[values < 0.0].sum()))
        periods: list[dict[str, Any]] = []
        if role == "development" and not selected.empty:
            for quarter, group in selected.groupby(selected["entry_dt"].dt.quarter):
                periods.append(
                    {
                        "period": f"Q{int(quarter)}",
                        "trades": int(len(group)),
                        "sum_r": float(group["realized_r"].sum()),
                    },
                )
        elif not selected.empty:
            for segment, group in selected.groupby("segment", sort=True):
                periods.append(
                    {
                        "period": str(segment),
                        "trades": int(len(group)),
                        "sum_r": float(group["realized_r"].sum()),
                    },
                )
        rows.append(
            {
                "role": role,
                "rule": name,
                "trades": int(len(values)),
                "sum_r": float(values.sum()),
                "mean_r": float(values.mean()) if len(values) else None,
                "win_rate": float((values > 0.0).mean()) if len(values) else None,
                "profit_factor": gross_profit / gross_loss if gross_loss > 0.0 else None,
                "periods": periods,
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
    warmup = max(int(research.get("warmup_minutes", 420)), candidate.range_minutes + 180)
    combined_rows: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        raw_frame, records = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache,
            warmup_minutes=warmup,
        )
        manifest.extend(asdict(record) for record in records)
        bars = to_auction_bars(raw_frame)
        trades, _, _ = simulate(
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
        if trades.empty:
            continue
        context = _event_context(bars, candidate)
        trades = trades.merge(context, on="scenario_id", how="left", validate="one_to_one")
        required = ["probe_time_ns", "displacement_time_ns", "probe_atr"]
        if trades[required].isna().any().any():
            raise RuntimeError(f"missing causal event context in segment {label}")
        bar_state = _causal_bar_state(raw_frame, candidate)
        sequence = _sequence_features(trades, bar_state)
        joined = trades.merge(sequence, on="scenario_id", how="left", validate="one_to_one")
        side_sign = joined["side"].map({"LONG": 1.0, "SHORT": -1.0}).astype(float)
        joined["trade_direction_displacement_flow_z"] = (
            joined["displacement_flow_z"] * side_sign
        )
        joined.insert(0, "segment", label)
        joined.insert(1, "role", role)
        joined["entry_dt"] = pd.to_datetime(joined["entry_time_ns"], unit="ns", utc=True)
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        joined.to_csv(destination / "trades_with_flow_regime.csv", index=False)
        combined_rows.append(joined)

    if not combined_rows:
        raise RuntimeError("flow-regime diagnostic produced no trades")
    combined = pd.concat(combined_rows, ignore_index=True)
    combined.to_csv(output / "combined_trades_with_flow_regime.csv", index=False)
    roles = ("development", "quick", "untouched")
    scores = [row for role in roles for row in _score(combined, role)]
    pd.DataFrame(scores).to_csv(output / "flow_regime_rule_scores.csv", index=False)
    files = pd.DataFrame(manifest).drop_duplicates(["symbol", "month"])
    _atomic_json(
        output / "download_manifest.json",
        {"provider": "Binance Vision", "records": files.to_dict(orient="records")},
    )
    summary = {
        "trades": int(len(combined)),
        "role_counts": combined["role"].value_counts().astype(int).to_dict(),
        "rules": scores,
        "causality": (
            "all sequence features stop at the completed displacement bar; "
            "entry remains one completed one-minute bar later"
        ),
    }
    _atomic_json(output / "flow_regime_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-flow-regime",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-flow-regime",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
