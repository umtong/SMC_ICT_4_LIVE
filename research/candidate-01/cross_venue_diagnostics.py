#!/usr/bin/env python3
"""Diagnose whether a failed auction is spot-led or derivatives-only.

A perpetual-contract sweep can reflect two different causal states:

* informed value relocation shared by spot and derivatives, or
* leveraged inventory liquidation / stop collection concentrated in the perp.

The existing candidate treats both states alike.  This diagnostic joins the
exact causal BTC failed-auction plans to Binance spot and USD-M perpetual
one-minute auctions.  Every feature is available by the completed displacement
bar and is therefore known before the existing one-bar delayed entry.

The rules below are deliberately structural and few in number.  They are not a
parameter search: they test spot non-confirmation of the swept boundary, basis
exhaustion and snap-back, and spot participation in the reversal displacement.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
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
from data import (  # noqa: E402
    _http_get,
    _month_starts,
    _publisher_checksum,
    _read_archive,
    load_interval,
    parse_utc_date,
    to_auction_bars,
)
from portfolio_probe import Variant, simulate  # noqa: E402


SPOT_BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"


@dataclass(frozen=True, slots=True)
class SpotDownload:
    symbol: str
    interval: str
    month: str
    url: str
    checksum_url: str
    path: str
    size_bytes: int
    sha256: str
    publisher_sha256: str | None


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


def _download_spot_month(
    *,
    symbol: str,
    interval: str,
    month: datetime,
    cache_dir: Path,
) -> tuple[pd.DataFrame, SpotDownload]:
    symbol = symbol.upper()
    month_text = month.strftime("%Y-%m")
    archive_name = f"{symbol}-{interval}-{month_text}.zip"
    csv_name = f"{symbol}-{interval}-{month_text}.csv"
    url = f"{SPOT_BASE_URL}/{symbol}/{interval}/{archive_name}"
    checksum_url = f"{url}.CHECKSUM"
    cache_dir.mkdir(parents=True, exist_ok=True)
    destination = cache_dir / archive_name
    if destination.is_file() and destination.stat().st_size > 0:
        payload = destination.read_bytes()
    else:
        payload = _http_get(url)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    local_digest = sha256(payload).hexdigest()
    publisher_digest: str | None = None
    try:
        checksum_text = _http_get(checksum_url).decode("utf-8", errors="strict")
        publisher_digest = _publisher_checksum(checksum_text, archive_name)
    except Exception:
        publisher_digest = None
    if publisher_digest is not None and publisher_digest != local_digest:
        raise RuntimeError(
            f"spot checksum mismatch for {archive_name}: "
            f"publisher={publisher_digest}, local={local_digest}",
        )
    frame = _read_archive(payload, csv_name)
    return frame, SpotDownload(
        symbol=symbol,
        interval=interval,
        month=month_text,
        url=url,
        checksum_url=checksum_url,
        path=str(destination),
        size_bytes=len(payload),
        sha256=local_digest,
        publisher_sha256=publisher_digest,
    )


def _load_spot_interval(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    cache_dir: Path,
    warmup_minutes: int,
) -> tuple[pd.DataFrame, list[SpotDownload]]:
    start = start.astimezone(timezone.utc)
    end = end.astimezone(timezone.utc)
    fetch_start = start - timedelta(minutes=warmup_minutes)
    frames: list[pd.DataFrame] = []
    records: list[SpotDownload] = []
    for month in _month_starts(fetch_start, end):
        frame, record = _download_spot_month(
            symbol=symbol,
            interval="1m",
            month=month,
            cache_dir=cache_dir,
        )
        frames.append(frame)
        records.append(record)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("open_dt", kind="stable").drop_duplicates("open_dt", keep="last")
    combined = combined.loc[
        (combined["open_dt"] >= fetch_start) & (combined["open_dt"] < end),
    ].reset_index(drop=True)
    if combined.empty:
        raise RuntimeError(f"no spot rows found for {symbol} in [{fetch_start}, {end})")
    deltas = combined["open_dt"].diff().dropna()
    gaps = deltas[deltas > pd.Timedelta(seconds=61)]
    if not gaps.empty:
        index = int(gaps.index[0])
        raise RuntimeError(
            f"spot minute continuity gap: {combined.loc[index - 1, 'open_dt']} -> "
            f"{combined.loc[index, 'open_dt']}",
        )
    return combined, records


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
                    "probe_time_ns": str(event.event_time_ns),
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
                    "displacement_time_ns": str(event.event_time_ns),
                    "displacement_flow_z": event.details.get("flow_z"),
                    "displacement_body_atr": event.details.get("body_atr"),
                    "structure_overshoot_atr": event.details.get("structure_overshoot_atr"),
                },
            )
    result = pd.DataFrame(rows.values())
    for column in ("probe_time_ns", "displacement_time_ns"):
        if column in result:
            result[column] = pd.array(
                [
                    int(value) if pd.notna(value) else pd.NA
                    for value in result[column]
                ],
                dtype="Int64",
            )
    return result


def _causal_z(series: pd.Series, window: int, minimum: int) -> pd.Series:
    history = series.rolling(window, min_periods=minimum)
    mean = history.mean().shift(1)
    std = history.std(ddof=0).shift(1).replace(0.0, np.nan)
    return (series - mean) / std


def _aligned_auctions(perp: pd.DataFrame, spot: pd.DataFrame) -> pd.DataFrame:
    perp_cols = [
        "open_dt",
        "close_dt",
        "open",
        "high",
        "low",
        "close",
        "quote_volume",
        "taker_buy_quote_volume",
    ]
    spot_cols = [
        "open_dt",
        "open",
        "high",
        "low",
        "close",
        "quote_volume",
        "taker_buy_quote_volume",
    ]
    left = perp[perp_cols].rename(
        columns={column: f"perp_{column}" for column in perp_cols if column != "open_dt"},
    )
    right = spot[spot_cols].rename(
        columns={column: f"spot_{column}" for column in spot_cols if column != "open_dt"},
    )
    frame = left.merge(right, on="open_dt", how="inner", validate="one_to_one")
    frame = frame.sort_values("open_dt", kind="stable").reset_index(drop=True)
    frame["event_time_ns"] = (
        pd.to_datetime(frame["perp_close_dt"], utc=True)
        .astype("datetime64[ns, UTC]")
        .astype("int64")
    )
    for venue in ("perp", "spot"):
        frame[f"{venue}_aggressive_imbalance"] = (
            2.0 * frame[f"{venue}_taker_buy_quote_volume"]
            - frame[f"{venue}_quote_volume"]
        ) / frame[f"{venue}_quote_volume"].replace(0.0, np.nan)
        frame[f"{venue}_flow_z_60"] = _causal_z(
            frame[f"{venue}_aggressive_imbalance"],
            60,
            20,
        )
        returns = np.log(frame[f"{venue}_close"]).diff()
        frame[f"{venue}_return"] = returns
        frame[f"{venue}_return_z_60"] = _causal_z(returns, 60, 20)
        frame[f"{venue}_volume_z_120"] = _causal_z(
            np.log(frame[f"{venue}_quote_volume"].where(frame[f"{venue}_quote_volume"] > 0.0)),
            120,
            40,
        )
    frame["perp_spot_ratio"] = frame["perp_close"] / frame["spot_close"]
    frame["perp_spot_ratio_lag1"] = frame["perp_spot_ratio"].shift(1)
    frame["basis_bps"] = (frame["perp_spot_ratio"] - 1.0) * 10_000.0
    frame["basis_z_360"] = _causal_z(frame["basis_bps"], 360, 120)
    return frame


def _prefix_at_times(times: pd.Series, auctions: pd.DataFrame, prefix: str) -> pd.DataFrame:
    events = pd.DataFrame(
        {
            "event_row": np.arange(len(times), dtype=int),
            "event_time_ns": pd.to_numeric(times, errors="raise").astype("int64"),
        },
    )
    columns = [
        "event_time_ns",
        "perp_open",
        "perp_high",
        "perp_low",
        "perp_close",
        "spot_open",
        "spot_high",
        "spot_low",
        "spot_close",
        "perp_flow_z_60",
        "spot_flow_z_60",
        "perp_return_z_60",
        "spot_return_z_60",
        "perp_volume_z_120",
        "spot_volume_z_120",
        "perp_spot_ratio",
        "perp_spot_ratio_lag1",
        "basis_bps",
        "basis_z_360",
    ]
    joined = events.merge(
        auctions[columns],
        on="event_time_ns",
        how="left",
        validate="one_to_one",
    ).sort_values("event_row", kind="stable")
    joined = joined.drop(columns=["event_row", "event_time_ns"]).reset_index(drop=True)
    return joined.rename(columns={column: f"{prefix}_{column}" for column in joined.columns})


def _derive(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    side_sign = result["side"].map({"LONG": 1.0, "SHORT": -1.0}).astype(float)
    breakout_sign = -side_sign
    ratio = result["probe_perp_spot_ratio_lag1"].where(
        result["probe_perp_spot_ratio_lag1"] > 0.0,
        result["probe_perp_spot_ratio"],
    )
    mapped_boundary = result["probe_boundary"] / ratio
    spot_penetration = np.where(
        side_sign > 0.0,
        mapped_boundary - result["probe_spot_low"],
        result["probe_spot_high"] - mapped_boundary,
    )
    perp_penetration = np.where(
        side_sign > 0.0,
        result["probe_boundary"] - result["probe_perp_low"],
        result["probe_perp_high"] - result["probe_boundary"],
    )
    result["spot_penetration_in_perp_atr"] = spot_penetration * ratio / result["probe_atr"]
    result["perp_penetration_atr"] = perp_penetration / result["probe_atr"]
    result["spot_nonconfirmation_atr"] = -result["spot_penetration_in_perp_atr"]
    result["spot_perp_penetration_ratio"] = (
        spot_penetration * ratio / np.maximum(perp_penetration, result["probe_atr"] * 1e-9)
    )
    result["breakout_aligned_basis_bps"] = breakout_sign * result["probe_basis_bps"]
    result["breakout_aligned_basis_z"] = breakout_sign * result["probe_basis_z_360"]
    result["basis_snap_bps"] = side_sign * (
        result["displacement_basis_bps"] - result["probe_basis_bps"]
    )
    result["probe_breakout_flow_divergence"] = breakout_sign * (
        result["probe_flow_z"] - result["probe_spot_flow_z_60"]
    )
    result["trade_direction_perp_flow_z"] = side_sign * result["displacement_flow_z"]
    result["trade_direction_spot_flow_z"] = side_sign * result["displacement_spot_flow_z_60"]
    result["trade_direction_spot_return_z"] = side_sign * result["displacement_spot_return_z_60"]
    result["trade_direction_perp_return_z"] = side_sign * result["displacement_perp_return_z_60"]
    spot_path = result["displacement_spot_close"] / result["probe_spot_close"] - 1.0
    perp_path = result["displacement_perp_close"] / result["probe_perp_close"] - 1.0
    result["spot_relative_lead_bps"] = side_sign * (spot_path - perp_path) * 10_000.0
    result["entry_basis_followthrough_bps"] = side_sign * (
        result["entry_basis_bps"] - result["displacement_basis_bps"]
    )
    result["entry_dt"] = pd.to_datetime(result["entry_time_ns"], unit="ns", utc=True)
    return result


def _rules(frame: pd.DataFrame) -> dict[str, pd.Series]:
    strong_flow = frame["trade_direction_perp_flow_z"] >= 1.70
    spot_did_not_sweep = frame["spot_penetration_in_perp_atr"] <= 0.0
    meaningful_nonconfirmation = frame["spot_nonconfirmation_atr"] >= 0.03
    basis_exhaustion = frame["breakout_aligned_basis_z"] >= 0.75
    basis_snap = frame["basis_snap_bps"] >= 0.0
    spot_reversal = frame["trade_direction_spot_flow_z"] >= 0.50
    spot_return = frame["trade_direction_spot_return_z"] >= 0.50
    derivative_dominance = frame["probe_breakout_flow_divergence"] >= 0.50
    return {
        "all": pd.Series(True, index=frame.index),
        "strong-flow-170": strong_flow,
        "spot-nonconfirmation": spot_did_not_sweep,
        "basis-exhaustion-snap": basis_exhaustion & basis_snap,
        "spot-led-displacement": (
            (frame["trade_direction_perp_flow_z"] >= 1.25)
            & spot_reversal
            & spot_return
            & basis_snap
        ),
        "derivatives-exhaustion": (
            meaningful_nonconfirmation
            & basis_exhaustion
            & derivative_dominance
            & basis_snap
        ),
        "cross-venue-failed-auction": (
            spot_did_not_sweep
            & (frame["trade_direction_perp_flow_z"] >= 1.25)
            & (frame["trade_direction_spot_flow_z"] >= 0.25)
            & basis_snap
        ),
        "strong-flow-or-cross-venue": strong_flow
        | (
            meaningful_nonconfirmation
            & (frame["trade_direction_spot_flow_z"] >= 0.25)
            & basis_snap
        ),
    }


def _score(frame: pd.DataFrame, role: str) -> list[dict[str, Any]]:
    source = frame.loc[frame["role"] == role].copy()
    rows: list[dict[str, Any]] = []
    for name, mask in _rules(source).items():
        selected = source.loc[mask.fillna(False)].copy()
        values = pd.to_numeric(selected["realized_r"], errors="coerce").dropna()
        gross_profit = float(values[values > 0.0].sum())
        gross_loss = abs(float(values[values < 0.0].sum()))
        by_period: list[dict[str, Any]] = []
        if role == "development" and not selected.empty:
            for quarter, group in selected.groupby(selected["entry_dt"].dt.quarter):
                by_period.append(
                    {
                        "period": f"Q{int(quarter)}",
                        "trades": int(len(group)),
                        "sum_r": float(group["realized_r"].sum()),
                    },
                )
        elif not selected.empty:
            for segment, group in selected.groupby("segment", sort=True):
                by_period.append(
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
                "periods": by_period,
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
    warmup = max(int(research.get("warmup_minutes", 420)), 720)
    combined_rows: list[pd.DataFrame] = []
    manifest: list[dict[str, Any]] = []

    for label, start, end, role in _segments(research):
        perp_frame, perp_records = load_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache / "perp",
            warmup_minutes=warmup,
        )
        spot_frame, spot_records = _load_spot_interval(
            symbol="BTCUSDT",
            start=start,
            end=end,
            cache_dir=args.cache / "spot",
            warmup_minutes=warmup,
        )
        manifest.extend({"market": "perpetual", **asdict(record)} for record in perp_records)
        manifest.extend({"market": "spot", **asdict(record)} for record in spot_records)
        bars = to_auction_bars(perp_frame)
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
        required = ["probe_time_ns", "displacement_time_ns", "entry_time_ns"]
        if trades[required].isna().any().any():
            raise RuntimeError(f"missing causal event context in segment {label}")
        auctions = _aligned_auctions(perp_frame, spot_frame)
        joined = pd.concat(
            [
                trades.reset_index(drop=True),
                _prefix_at_times(trades["probe_time_ns"], auctions, "probe"),
                _prefix_at_times(trades["displacement_time_ns"], auctions, "displacement"),
                _prefix_at_times(trades["entry_time_ns"], auctions, "entry"),
            ],
            axis=1,
        )
        joined.insert(0, "segment", label)
        joined.insert(1, "role", role)
        joined = _derive(joined)
        destination = output / label
        destination.mkdir(parents=True, exist_ok=True)
        joined.to_csv(destination / "trades_with_cross_venue_state.csv", index=False)
        combined_rows.append(joined)

    if not combined_rows:
        raise RuntimeError("cross-venue diagnostic produced no trades")
    combined = pd.concat(combined_rows, ignore_index=True)
    combined.to_csv(output / "combined_trades_with_cross_venue_state.csv", index=False)
    roles = ("development", "quick", "untouched")
    scores = [row for role in roles for row in _score(combined, role)]
    pd.DataFrame(scores).to_csv(output / "cross_venue_rule_scores.csv", index=False)
    files = pd.DataFrame(manifest).drop_duplicates(["market", "symbol", "month"])
    _atomic_json(
        output / "cross_venue_manifest.json",
        {"provider": "Binance Vision", "files": files.to_dict(orient="records")},
    )
    summary = {
        "trades": int(len(combined)),
        "role_counts": combined["role"].value_counts().astype(int).to_dict(),
        "rules": scores,
        "causality": (
            "probe and displacement features use completed bars only; "
            "the existing entry remains delayed by one completed one-minute bar"
        ),
    }
    _atomic_json(output / "cross_venue_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=HERE / "config.json")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / ".cache" / "candidate-01-cross-venue",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "candidate-01-cross-venue",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
