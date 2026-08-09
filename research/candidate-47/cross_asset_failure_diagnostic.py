#!/usr/bin/env python3
"""Cross-asset state diagnostic for the reusable Candidate 16 failure sequence.

This is not an execution engine and makes no account-performance claim.  It
joins 17 already-executed BTC component trades to checksum-verified Binance
monthly one-minute bars for BTC, ETH, SOL and XRP.  The only question is whether
information available by the entry timestamp can distinguish an idiosyncratic
failed auction from a systemic market repricing which should not be faded.

All candidate state predicates are categorical sign/sequence rules.  The script
does not search numeric thresholds or optimize PnL.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import sys
import urllib.request
from typing import Any, Iterable

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CANDIDATE05 = HERE.parent / "candidate-05"
sys.path.insert(0, str(CANDIDATE05))

from features import read_kline  # noqa: E402


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
PEERS = ("ETHUSDT", "SOLUSDT", "XRPUSDT")
BASE = "https://data.binance.vision/data/futures/um/monthly/klines"


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _download_checked(symbol: str, month: str, cache: Path) -> tuple[Path, dict[str, Any]]:
    filename = f"{symbol}-1m-{month}.zip"
    url = f"{BASE}/{symbol}/1m/{filename}"
    directory = cache / symbol
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = sha256_file(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive, {
        "symbol": symbol,
        "month": month,
        "archive": str(archive),
        "checksum": str(checksum),
        "sha256": actual,
        "size_bytes": archive.stat().st_size,
    }


def _load_bars(cases: list[dict[str, Any]], cache: Path) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    months = sorted(
        {
            pd.Timestamp(int(case["interaction_ts_event_ns"]), unit="ns", tz="UTC").strftime("%Y-%m")
            for case in cases
        },
    )
    evidence: list[dict[str, Any]] = []
    result: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        frames: list[pd.DataFrame] = []
        for month in months:
            archive, record = _download_checked(symbol, month, cache)
            evidence.append(record)
            frames.append(read_kline(archive))
        frame = pd.concat(frames, ignore_index=True)
        frame["close_time_dt"] = pd.to_datetime(frame["close_time_dt"], utc=True, errors="raise")
        frame = frame.sort_values("close_time_dt").drop_duplicates("close_time_dt", keep=False)
        if frame.empty or frame["close_time_dt"].duplicated().any():
            raise RuntimeError(f"invalid monthly kline assembly for {symbol}")
        frame = frame.set_index("close_time_dt")
        result[symbol] = frame[["open", "high", "low", "close", "volume"]].astype(float)
    return result, evidence


def _at_or_before(frame: pd.DataFrame, timestamp: pd.Timestamp) -> int:
    index = frame.index.searchsorted(timestamp, side="right") - 1
    if index < 0:
        raise RuntimeError(f"no causal bar at or before {timestamp}")
    observed = frame.index[index]
    if timestamp - observed > pd.Timedelta(seconds=1):
        raise RuntimeError(f"stale kline at {timestamp}: latest={observed}")
    return int(index)


def _return_bps(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    left = _at_or_before(frame, start)
    right = _at_or_before(frame, end)
    if right <= left:
        return 0.0
    first = float(frame.iloc[left]["close"])
    last = float(frame.iloc[right]["close"])
    if first <= 0.0 or last <= 0.0:
        return math.nan
    return math.log(last / first) * 10_000.0


def _path_shape(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, float]:
    left = _at_or_before(frame, start)
    right = _at_or_before(frame, end)
    closes = frame.iloc[left : right + 1]["close"].astype(float).to_numpy()
    if len(closes) < 2 or np.any(closes <= 0.0):
        return {"net_bps": 0.0, "continuity": 0.0, "largest_step_share": 1.0}
    steps = np.diff(np.log(closes)) * 10_000.0
    path = float(np.abs(steps).sum())
    net = float(np.log(closes[-1] / closes[0]) * 10_000.0)
    if path <= 0.0:
        return {"net_bps": net, "continuity": 0.0, "largest_step_share": 1.0}
    return {
        "net_bps": net,
        "continuity": abs(net) / path,
        "largest_step_share": float(np.max(np.abs(steps)) / path),
    }


def _majority(values: Iterable[float]) -> bool:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return sum(value > 0.0 for value in clean) >= 2


def _all_positive(values: Iterable[float]) -> bool:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return len(clean) == 3 and all(value > 0.0 for value in clean)


def enrich_case(case: dict[str, Any], bars: dict[str, pd.DataFrame]) -> dict[str, Any]:
    interaction = pd.Timestamp(int(case["interaction_ts_event_ns"]), unit="ns", tz="UTC")
    entry = pd.Timestamp(int(case["entry_ts_event_ns"]), unit="ns", tz="UTC")
    parent_direction = int(case["parent_direction"])
    position_side = int(case["position_side"])
    if parent_direction not in (-1, 1) or position_side not in (-1, 1):
        raise ValueError("case direction is invalid")

    result = dict(case)
    result["interaction_utc"] = interaction.isoformat()
    result["entry_utc"] = entry.isoformat()
    result["decision_delay_minutes"] = (entry - interaction).total_seconds() / 60.0

    for horizon in (5, 15, 60):
        pre_end = interaction - pd.Timedelta(minutes=1)
        pre_start = pre_end - pd.Timedelta(minutes=horizon)
        through_start = interaction - pd.Timedelta(minutes=horizon)
        pre_returns: dict[str, float] = {}
        through_returns: dict[str, float] = {}
        for symbol in SYMBOLS:
            pre_returns[symbol] = _return_bps(bars[symbol], pre_start, pre_end)
            through_returns[symbol] = _return_bps(bars[symbol], through_start, interaction)
        peer_pre = [parent_direction * pre_returns[symbol] for symbol in PEERS]
        peer_through = [parent_direction * through_returns[symbol] for symbol in PEERS]
        result[f"parent_peer_pre_{horizon}m_median_bps"] = float(np.median(peer_pre))
        result[f"parent_peer_pre_{horizon}m_breadth"] = sum(value > 0.0 for value in peer_pre)
        result[f"parent_peer_through_{horizon}m_median_bps"] = float(np.median(peer_through))
        result[f"parent_peer_through_{horizon}m_breadth"] = sum(value > 0.0 for value in peer_through)
        btc_aligned = parent_direction * through_returns["BTCUSDT"]
        result[f"btc_parent_through_{horizon}m_bps"] = btc_aligned
        result[f"btc_parent_residual_{horizon}m_bps"] = btc_aligned - float(np.median(peer_through))

    event_start = interaction - pd.Timedelta(minutes=1)
    event_returns = {
        symbol: _return_bps(bars[symbol], event_start, interaction)
        for symbol in SYMBOLS
    }
    aligned_peer_event = [parent_direction * event_returns[symbol] for symbol in PEERS]
    result["parent_peer_event_median_bps"] = float(np.median(aligned_peer_event))
    result["parent_peer_event_breadth"] = sum(value > 0.0 for value in aligned_peer_event)
    result["btc_parent_event_bps"] = parent_direction * event_returns["BTCUSDT"]
    result["btc_parent_event_residual_bps"] = (
        result["btc_parent_event_bps"] - result["parent_peer_event_median_bps"]
    )

    initiative_returns = {
        symbol: _return_bps(bars[symbol], interaction, entry)
        for symbol in SYMBOLS
    }
    aligned_peer_initiative = [position_side * initiative_returns[symbol] for symbol in PEERS]
    result["initiative_peer_median_bps"] = float(np.median(aligned_peer_initiative))
    result["initiative_peer_breadth"] = sum(value > 0.0 for value in aligned_peer_initiative)
    result["btc_initiative_bps"] = position_side * initiative_returns["BTCUSDT"]
    result["btc_initiative_residual_bps"] = (
        result["btc_initiative_bps"] - result["initiative_peer_median_bps"]
    )

    peer_shapes = [
        _path_shape(
            bars[symbol],
            interaction - pd.Timedelta(minutes=15),
            interaction,
        )
        for symbol in PEERS
    ]
    result["peer_15m_continuity_median"] = float(
        np.median([shape["continuity"] for shape in peer_shapes]),
    )
    result["peer_15m_largest_step_share_median"] = float(
        np.median([shape["largest_step_share"] for shape in peer_shapes]),
    )

    # Categorical hypotheses use no fitted magnitudes.
    result["attack_peer_majority_event"] = _majority(aligned_peer_event)
    result["attack_peer_unanimous_event"] = _all_positive(aligned_peer_event)
    result["attack_peer_majority_5m"] = (
        int(result["parent_peer_through_5m_breadth"]) >= 2
    )
    result["attack_peer_majority_15m"] = (
        int(result["parent_peer_through_15m_breadth"]) >= 2
    )
    result["later_initiative_peer_majority"] = _majority(aligned_peer_initiative)
    result["later_initiative_peer_unanimous"] = _all_positive(aligned_peer_initiative)
    result["btc_attack_idiosyncratic_event"] = (
        result["btc_parent_event_bps"] > 0.0
        and result["parent_peer_event_breadth"] <= 1
        and result["btc_parent_event_residual_bps"] > 0.0
    )
    result["cross_market_state_flip"] = (
        not result["attack_peer_majority_event"]
        and result["later_initiative_peer_majority"]
    )
    result["systemic_attack_without_reversal_support"] = (
        result["attack_peer_majority_event"]
        and not result["later_initiative_peer_majority"]
    )
    return result


def summarize_flag(cases: list[dict[str, Any]], flag: str) -> dict[str, Any]:
    selected = [case for case in cases if bool(case.get(flag))]
    by_period: dict[str, dict[str, float | int]] = {}
    for case in selected:
        period = str(case["source_label"])
        bucket = by_period.setdefault(period, {"trades": 0, "wins": 0, "pnl_usdt": 0.0})
        bucket["trades"] = int(bucket["trades"]) + 1
        bucket["wins"] = int(bucket["wins"]) + int(bool(case["win"]))
        bucket["pnl_usdt"] = float(bucket["pnl_usdt"]) + float(case["realized_pnl_usdt"])
    return {
        "flag": flag,
        "trades": len(selected),
        "wins": sum(bool(case["win"]) for case in selected),
        "win_rate": (
            sum(bool(case["win"]) for case in selected) / len(selected)
            if selected
            else 0.0
        ),
        "pnl_usdt": sum(float(case["realized_pnl_usdt"]) for case in selected),
        "source_periods": len(by_period),
        "positive_source_periods": sum(float(item["pnl_usdt"]) > 0.0 for item in by_period.values()),
        "by_period": by_period,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=HERE / "evidence" / "c16-historical-trade-cases.json",
    )
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)

    source = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = list(source["cases"])
    bars, evidence = _load_bars(cases, args.cache)
    enriched = [enrich_case(case, bars) for case in cases]
    rejection = [case for case in enriched if case["branch"] == "REJECTION"]

    flags = (
        "attack_peer_majority_event",
        "attack_peer_unanimous_event",
        "attack_peer_majority_5m",
        "attack_peer_majority_15m",
        "later_initiative_peer_majority",
        "later_initiative_peer_unanimous",
        "btc_attack_idiosyncratic_event",
        "cross_market_state_flip",
        "systemic_attack_without_reversal_support",
    )
    summaries = [summarize_flag(rejection, flag) for flag in flags]
    summaries.sort(
        key=lambda item: (
            int(item["positive_source_periods"]),
            int(item["source_periods"]),
            float(item["pnl_usdt"]),
            int(item["trades"]),
        ),
        reverse=True,
    )

    result = {
        "schema": "candidate35-cross-asset-failure-diagnostic-v1",
        "claim_scope": "HISTORICAL_COMPONENT_DIAGNOSTIC_NO_NEW_ACCOUNT_PNL_CLAIM",
        "symbols": list(SYMBOLS),
        "cases": len(enriched),
        "rejection_cases": len(rejection),
        "source_periods": sorted({str(case["source_label"]) for case in enriched}),
        "categorical_flag_summaries": summaries,
        "cases_enriched": enriched,
        "source_evidence": evidence,
    }
    (args.output / "cross_asset_failure_diagnostic.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pd.DataFrame(enriched).to_csv(
        args.output / "cross_asset_failure_cases.csv",
        index=False,
    )
    print(json.dumps({key: value for key, value in result.items() if key != "cases_enriched" and key != "source_evidence"}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
