#!/usr/bin/env python3
"""Causal derivatives-state anatomy for the stable public hourly impulse family.

The public UTBot investigation found a recurring after-cost path in completed
one-hour impulse episodes, but not enough information to know when the move is a
sponsored repricing, forced unwind, or exhaustion. This experiment freezes two
already-observed event families and asks whether derivatives sponsorship
explains continuation versus reversal. No parameter optimization occurs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any
import urllib.request

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
EVENT_FAMILIES = ("public_vectorized_no_ema", "impulse_only_2atr")
HORIZONS_MIN = (15, 30, 60, 120, 240, 480, 720)
COST_BPS = 19.0
FUTURES_BASE = "https://data.binance.vision/data/futures/um/daily"
METRIC_COLUMNS = (
    "create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
    "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
    "count_long_short_ratio", "sum_taker_long_short_vol_ratio",
)
KLINE_COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trade_count", "taker_buy_base", "taker_buy_quote", "ignore",
)


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _checked_download(relative: str, cache: Path) -> tuple[Path, dict[str, Any]]:
    url = f"{FUTURES_BASE}/{relative}"
    filename = Path(relative).name
    directory = cache / Path(relative).parent
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / filename
    checksum = directory / f"{filename}.CHECKSUM"
    if not archive.exists():
        urllib.request.urlretrieve(url, archive)
    if not checksum.exists():
        urllib.request.urlretrieve(url + ".CHECKSUM", checksum)
    expected = checksum.read_text().strip().split()[0].lower()
    actual = _sha256(archive)
    if actual != expected:
        raise RuntimeError(f"checksum mismatch for {archive}: {actual} != {expected}")
    return archive, {
        "url": url, "archive": str(archive), "checksum": str(checksum),
        "bytes": archive.stat().st_size, "sha256": actual,
    }


def _timestamp_unit(values: pd.Series) -> str:
    first = int(pd.to_numeric(values, errors="raise").iloc[0])
    if abs(first) > 10**16:
        return "ns"
    if abs(first) > 10**13:
        return "us"
    return "ms"


def _read_metrics(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, compression="zip")
    if not set(METRIC_COLUMNS).issubset(frame.columns):
        raw = pd.read_csv(path, compression="zip", header=None)
        if raw.shape[1] < len(METRIC_COLUMNS):
            raise RuntimeError(f"unexpected metrics schema in {path}: {raw.shape}")
        raw = raw.iloc[:, : len(METRIC_COLUMNS)]
        raw.columns = METRIC_COLUMNS
        if str(raw.iloc[0]["create_time"]).strip().lower() == "create_time":
            raw = raw.iloc[1:].copy()
        frame = raw
    frame["time"] = pd.to_datetime(frame["create_time"], utc=True, errors="raise")
    for column in (
        "sum_open_interest", "sum_open_interest_value",
        "sum_taker_long_short_vol_ratio",
        "sum_toptrader_long_short_ratio", "count_long_short_ratio",
    ):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.sort_values("time").drop_duplicates("time", keep="last").set_index("time")


def _read_premium(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, compression="zip", header=None)
    if raw.shape[1] < len(KLINE_COLUMNS):
        with_header = pd.read_csv(path, compression="zip")
        if not set(KLINE_COLUMNS).issubset(with_header.columns):
            raise RuntimeError(f"unexpected premium schema in {path}")
        raw = with_header[list(KLINE_COLUMNS)].copy()
    else:
        raw = raw.iloc[:, : len(KLINE_COLUMNS)]
        raw.columns = KLINE_COLUMNS
        first = str(raw.iloc[0]["open_time"]).strip()
        if not first.lstrip("-").isdigit():
            raw = raw.iloc[1:].copy()
    for column in ("open_time", "close_time", "open", "high", "low", "close"):
        raw[column] = pd.to_numeric(raw[column], errors="raise")
    raw["time"] = pd.to_datetime(
        raw["close_time"].astype("int64"), unit=_timestamp_unit(raw["close_time"]),
        utc=True, errors="raise",
    )
    raw["premium"] = raw["close"].astype(float)
    return raw[["time", "premium"]].sort_values("time").drop_duplicates("time").set_index("time")


def _load_derivatives(symbol: str, start: date, end: date, cache: Path) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]]]:
    metrics_frames, premium_frames, evidence = [], [], []
    day = start
    while day <= end:
        stamp = day.isoformat()
        metrics_path, metrics_evidence = _checked_download(
            f"metrics/{symbol}/{symbol}-metrics-{stamp}.zip", cache
        )
        premium_path, premium_evidence = _checked_download(
            f"premiumIndexKlines/{symbol}/5m/{symbol}-5m-{stamp}.zip", cache
        )
        metrics_frames.append(_read_metrics(metrics_path))
        premium_frames.append(_read_premium(premium_path))
        evidence.extend([metrics_evidence, premium_evidence])
        day += timedelta(days=1)
    metrics = pd.concat(metrics_frames).sort_index()
    metrics = metrics[~metrics.index.duplicated(keep="last")]
    premium = pd.concat(premium_frames).sort_index()
    premium = premium[~premium.index.duplicated(keep="last")]
    return metrics, premium, evidence


def _state_series(metrics: pd.DataFrame, premium: pd.DataFrame) -> pd.DataFrame:
    frame = metrics.copy()
    oi = frame["sum_open_interest_value"].where(frame["sum_open_interest_value"] > 0.0)
    frame["log_oi"] = np.log(oi)
    frame["oi_change_1h"] = frame["log_oi"] - frame["log_oi"].shift(12)
    frame["oi_abs_baseline"] = (
        frame["oi_change_1h"].abs().shift(1).rolling(288, min_periods=144).median()
    )
    taker = frame["sum_taker_long_short_vol_ratio"].where(
        frame["sum_taker_long_short_vol_ratio"] > 0.0
    )
    frame["log_taker"] = np.log(taker)
    frame["taker_1h"] = frame["log_taker"].rolling(12, min_periods=6).mean()
    joined = frame[["oi_change_1h", "oi_abs_baseline", "taker_1h"]].join(
        premium[["premium"]], how="outer"
    ).sort_index().ffill()
    joined["premium_change_1h"] = joined["premium"] - joined["premium"].shift(12)
    return joined


def _path_returns(minute: pd.DataFrame, entry_time: pd.Timestamp, side: int) -> dict[str, Any] | None:
    times = pd.DatetimeIndex(pd.to_datetime(minute["close_time_dt"], utc=True))
    start = int(times.searchsorted(entry_time, side="right"))
    if start >= len(minute):
        return None
    entry = float(minute.iloc[start]["open"])
    if not math.isfinite(entry) or entry <= 0.0:
        return None
    result: dict[str, Any] = {"entry_time": times[start], "entry_price": entry}
    for horizon in HORIZONS_MIN:
        end = int(times.searchsorted(times[start] + pd.Timedelta(minutes=horizon), side="left"))
        if end >= len(minute):
            result[f"cont_{horizon}m"] = None
            result[f"rev_{horizon}m"] = None
            continue
        exit_price = float(minute.iloc[end]["open"])
        gross = side * (exit_price / entry - 1.0)
        result[f"cont_{horizon}m"] = gross - COST_BPS / 10000.0
        result[f"rev_{horizon}m"] = -gross - COST_BPS / 10000.0
    end = int(times.searchsorted(times[start] + pd.Timedelta(minutes=720), side="right"))
    path = minute.iloc[start:min(end, len(minute))]
    if not path.empty:
        favourable = path["high"] / entry - 1.0 if side > 0 else 1.0 - path["low"] / entry
        adverse = path["low"] / entry - 1.0 if side > 0 else 1.0 - path["high"] / entry
        result["mfe_12h"] = float(favourable.max())
        result["mae_12h"] = float(adverse.min())
    return result


def _event_state(*, symbol: str, event_time: pd.Timestamp, side: int,
                 hourly_by_symbol: dict[str, pd.DataFrame], minute: pd.DataFrame,
                 derivatives: pd.DataFrame) -> dict[str, Any] | None:
    state_loc = int(derivatives.index.searchsorted(event_time, side="right")) - 1
    if state_loc < 12:
        return None
    row = derivatives.iloc[state_loc]
    oi_change = float(row["oi_change_1h"])
    oi_baseline = float(row["oi_abs_baseline"])
    taker = float(row["taker_1h"])
    premium_change = float(row["premium_change_1h"])
    if not all(math.isfinite(v) for v in (oi_change, oi_baseline, taker, premium_change)):
        return None

    own_hour = hourly_by_symbol[symbol]
    own_times = pd.DatetimeIndex(pd.to_datetime(own_hour["close_time"], utc=True))
    own_loc = int(own_times.searchsorted(event_time, side="right")) - 1
    if own_loc < 1:
        return None
    own = own_hour.iloc[own_loc]
    impulse_atr = side * (float(own["close"]) - float(own_hour.iloc[own_loc - 1]["close"])) / max(float(own["atr"]), 1e-12)
    breadth = 0
    impulse_breadth = 0
    for peer_hour in hourly_by_symbol.values():
        peer_times = pd.DatetimeIndex(pd.to_datetime(peer_hour["close_time"], utc=True))
        loc = int(peer_times.searchsorted(event_time, side="right")) - 1
        if loc < 1:
            continue
        current = peer_hour.iloc[loc]
        prior = peer_hour.iloc[loc - 1]
        peer_return = side * (float(current["close"]) / float(prior["close"]) - 1.0)
        peer_atr = float(current["atr"])
        if peer_return > 0.0:
            breadth += 1
        if peer_atr > 0.0 and side * (float(current["close"]) - float(prior["close"])) / peer_atr >= 0.5:
            impulse_breadth += 1

    minute_times = pd.DatetimeIndex(pd.to_datetime(minute["close_time_dt"], utc=True))
    event_loc = int(minute_times.searchsorted(event_time, side="right")) - 1
    prior15_loc = event_loc - 15
    if event_loc < 0 or prior15_loc < 0:
        return None
    last15 = side * (float(minute.iloc[event_loc]["close"]) / float(minute.iloc[prior15_loc]["close"]) - 1.0)
    oi_material = abs(oi_change) >= max(oi_baseline, 1e-12)
    oi_mode = "BUILD" if oi_change > 0.0 else "UNWIND"
    taker_aligned = side * taker > 0.0
    premium_aligned = side * premium_change > 0.0
    accepted = last15 > 0.0
    broad = breadth >= 2

    if oi_material and oi_mode == "BUILD" and taker_aligned and premium_aligned:
        state = "SPONSORED_BUILD_BROAD" if broad else "SPONSORED_BUILD_IDIOSYNCRATIC"
    elif oi_material and oi_mode == "UNWIND" and taker_aligned:
        state = "FORCED_UNWIND_ACCEPTED" if accepted else "FORCED_UNWIND_REJECTED"
    elif taker_aligned and premium_aligned and broad:
        state = "FLOW_ALIGNED_PRICE_ONLY"
    elif not taker_aligned and not premium_aligned:
        state = "UNSPONSORED_CONFLICT"
    else:
        state = "MIXED_SPONSORSHIP"

    delay_time = event_time + pd.Timedelta(minutes=15)
    delayed_loc = int(derivatives.index.searchsorted(delay_time, side="right")) - 1
    delayed_minute_loc = int(minute_times.searchsorted(delay_time, side="right")) - 1
    transition = "UNAVAILABLE"
    if delayed_loc >= state_loc and delayed_minute_loc > event_loc:
        future_taker = float(derivatives.iloc[delayed_loc]["taker_1h"])
        price15 = side * (float(minute.iloc[delayed_minute_loc]["close"]) / float(minute.iloc[event_loc]["close"]) - 1.0)
        flow15_aligned = side * future_taker > 0.0
        if price15 > 0.0 and flow15_aligned:
            transition = "PERSISTENT_15"
        elif price15 < 0.0 and not flow15_aligned:
            transition = "REJECTED_15"
        else:
            transition = "MIXED_15"

    return {
        "state": state, "transition15": transition,
        "oi_change_1h": oi_change, "oi_abs_baseline": oi_baseline,
        "oi_material": oi_material, "oi_mode": oi_mode,
        "taker_1h": taker, "taker_aligned": taker_aligned,
        "premium_change_1h": premium_change, "premium_aligned": premium_aligned,
        "breadth": breadth, "impulse_breadth": impulse_breadth,
        "last15_aligned_return": last15, "accepted_last15": accepted,
        "impulse_atr": impulse_atr,
    }


def _profit_factor(values: np.ndarray) -> float | None:
    gains = float(values[values > 0.0].sum())
    losses = float(-values[values < 0.0].sum())
    return None if losses <= 0.0 else gains / losses


def _summary(values: pd.Series) -> dict[str, Any]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy(float)
    if clean.size == 0:
        return {"count": 0}
    return {
        "count": int(clean.size), "mean": float(clean.mean()),
        "median": float(np.median(clean)), "win_rate": float(np.mean(clean > 0.0)),
        "profit_factor": _profit_factor(clean),
        "gross_profit": float(clean[clean > 0.0].sum()),
        "gross_loss": float(-clean[clean < 0.0].sum()),
        "q10": float(np.quantile(clean, 0.1)), "q90": float(np.quantile(clean, 0.9)),
    }


def _global_nonoverlap(frame: pd.DataFrame, horizon: int, entry_mode: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    entry_column = "entry_time" if entry_mode == "direct" else "delayed_entry_time"
    ordered = frame.sort_values([entry_column, "impulse_atr", "breadth", "symbol"], ascending=[True, False, False, True], kind="stable")
    selected, occupied_until = [], None
    for idx, row in ordered.iterrows():
        entry = pd.Timestamp(row[entry_column])
        if occupied_until is not None and entry < occupied_until:
            continue
        selected.append(idx)
        occupied_until = entry + pd.Timedelta(minutes=horizon)
    return frame.loc[selected].copy()


def run_one(args: argparse.Namespace) -> None:
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    warm_start = start - timedelta(days=args.warmup_days)
    forward_end = end + timedelta(days=args.forward_days)
    candidate51 = Path(args.candidate51_path)
    kline_module = _load_module(candidate51 / "kline_only_inputs.py", "c51_deriv_kline")
    utbot = _load_module(candidate51 / "utbot_impulse_anatomy.py", "c51_deriv_utbot")
    minute_by_symbol, hourly_by_symbol, signals_by_symbol, derivatives_by_symbol = {}, {}, {}, {}
    raw_evidence: dict[str, Any] = {}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    for symbol in SYMBOLS:
        minute, _, _, kline_evidence = kline_module.load_range(
            symbol=symbol, start=warm_start, end=forward_end,
            cache=Path(args.cache) / "klines" / symbol,
            output=output / "source" / symbol,
        )
        hourly = utbot._hourly(minute)
        signals = utbot._signals(hourly, utbot.PublicParams())
        metrics, premium, derivative_evidence = _load_derivatives(symbol, warm_start, forward_end, Path(args.cache) / "derivatives")
        minute_by_symbol[symbol] = minute
        hourly_by_symbol[symbol] = signals
        signals_by_symbol[symbol] = signals
        derivatives_by_symbol[symbol] = _state_series(metrics, premium)
        raw_evidence[symbol] = {"klines": [asdict(item) for item in kline_evidence], "derivatives": derivative_evidence}

    events: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        signals = signals_by_symbol[symbol]
        for family in EVENT_FAMILIES:
            side_column = f"side__{family}"
            for row in signals[signals[side_column].ne(0)].itertuples():
                event_time = pd.Timestamp(row.close_time)
                if not (start <= event_time.date() <= end):
                    continue
                side = int(getattr(row, side_column))
                state = _event_state(symbol=symbol, event_time=event_time, side=side,
                    hourly_by_symbol=hourly_by_symbol, minute=minute_by_symbol[symbol],
                    derivatives=derivatives_by_symbol[symbol])
                direct = _path_returns(minute_by_symbol[symbol], event_time, side)
                delayed = _path_returns(minute_by_symbol[symbol], event_time + pd.Timedelta(minutes=15), side)
                if state is None or direct is None or delayed is None:
                    continue
                record = {
                    "symbol": symbol, "period_label": args.period_label, "split": args.split,
                    "family": family, "event_time": event_time,
                    "event_id": f"{symbol}:{family}:{event_time.isoformat()}", "side": side,
                    "atr": float(row.atr), "adx": float(row.adx),
                    "volume_ratio": float(row.volume_ratio_long if side > 0 else row.volume_ratio_short),
                    **state, **direct,
                    "delayed_entry_time": delayed["entry_time"],
                    "delayed_entry_price": delayed["entry_price"],
                }
                for horizon in HORIZONS_MIN:
                    record[f"delayed_cont_{horizon}m"] = delayed.get(f"cont_{horizon}m")
                    record[f"delayed_rev_{horizon}m"] = delayed.get(f"rev_{horizon}m")
                events.append(record)

    payload = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "period_label": args.period_label, "split": args.split,
        "start": args.start, "end": args.end,
        "warm_start": warm_start.isoformat(), "forward_end": forward_end.isoformat(),
        "event_families": EVENT_FAMILIES,
        "state_contract": {
            "sponsored_build": "material OI build plus aligned taker and premium",
            "forced_unwind": "material OI decline plus aligned taker; last 15m acceptance separates accepted/rejected",
            "transition15": "price and taker flow observed through +15m classify persistence/rejection",
            "material_oi": "absolute 1h OI change >= prior trailing-24h median absolute 1h change",
        },
        "round_trip_cost_bps": COST_BPS,
        "raw_evidence": raw_evidence, "events": events,
    }
    (output / "result.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=lambda x: x.isoformat() if isinstance(x, pd.Timestamp) else x) + "\n")
    print(json.dumps({"period": args.period_label, "events": len(events)}, indent=2))


def aggregate(args: argparse.Namespace) -> None:
    paths = sorted(Path(args.results_root).rglob("result.json"))
    if not paths:
        raise RuntimeError("no result files")
    payloads = [json.loads(path.read_text()) for path in paths]
    events = pd.concat([pd.DataFrame(payload["events"]) for payload in payloads], ignore_index=True)
    if events.empty:
        raise RuntimeError("no events")
    for column in ("event_time", "entry_time", "delayed_entry_time"):
        events[column] = pd.to_datetime(events[column], utc=True)
    summary: dict[str, Any] = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "source_runs": len(payloads), "periods": sorted(events.period_label.unique()),
        "splits": sorted(events.split.unique()), "symbols": sorted(events.symbol.unique()),
        "event_families": list(EVENT_FAMILIES), "round_trip_cost_bps": COST_BPS,
        "predictions": {
            "sponsored_build": "continuation survives costs across chronological splits",
            "forced_unwind_accepted": "short-horizon continuation, weaker long-horizon continuation",
            "forced_unwind_rejected": "delayed reversal dominates delayed continuation",
            "unsponsored_conflict": "neither direction has stable positive expectancy",
        }, "groups": {}, "global_slots": {},
    }
    group_specs: list[tuple[str, pd.DataFrame]] = [("all", events)]
    group_specs += [(f"split:{k}", v) for k, v in events.groupby("split", sort=True)]
    group_specs += [(f"family:{k}", v) for k, v in events.groupby("family", sort=True)]
    group_specs += [(f"state:{k}", v) for k, v in events.groupby("state", sort=True)]
    group_specs += [(f"transition:{k}", v) for k, v in events.groupby("transition15", sort=True)]
    group_specs += [(f"split_state:{a}:{b}", v) for (a, b), v in events.groupby(["split", "state"], sort=True)]
    group_specs += [(f"split_transition:{a}:{b}", v) for (a, b), v in events.groupby(["split", "transition15"], sort=True)]
    for name, frame in group_specs:
        payload: dict[str, Any] = {"events": int(len(frame))}
        for horizon in HORIZONS_MIN:
            for column in (f"cont_{horizon}m", f"rev_{horizon}m", f"delayed_cont_{horizon}m", f"delayed_rev_{horizon}m"):
                payload[column] = _summary(frame[column])
        summary["groups"][name] = payload

    for family in EVENT_FAMILIES:
        family_frame = events[events.family.eq(family)]
        for horizon in (120, 240, 480, 720):
            for entry_mode, prefix in (("direct", ""), ("delayed", "delayed_")):
                selected = _global_nonoverlap(family_frame, horizon, entry_mode)
                days = len({pd.Timestamp(value).date() for value in events.event_time})
                key = f"{family}:{entry_mode}:{horizon}m"
                summary["global_slots"][key] = {
                    "calendar_days": days, "events": int(len(selected)),
                    "events_per_day": len(selected) / max(days, 1),
                    "continuation": _summary(selected[f"{prefix}cont_{horizon}m"]),
                    "reversal": _summary(selected[f"{prefix}rev_{horizon}m"]),
                    "state_counts": selected.state.value_counts().sort_index().to_dict(),
                    "transition_counts": selected.transition15.value_counts().sort_index().to_dict(),
                }

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "ANATOMY.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    events.to_csv(output / "EVENTS.csv", index=False)
    rows = []
    for name, payload in summary["groups"].items():
        for horizon in HORIZONS_MIN:
            for policy in ("cont", "rev", "delayed_cont", "delayed_rev"):
                rows.append({"group": name, "policy": policy, "horizon_min": horizon, **payload[f"{policy}_{horizon}m"]})
    pd.DataFrame(rows).to_csv(output / "SUMMARY.csv", index=False)

    md = [
        "# Derivatives sponsorship anatomy for hourly impulse episodes", "",
        f"- source periods: {len(payloads)}", f"- events: {len(events)}",
        f"- assets: {', '.join(summary['symbols'])}",
        f"- cost screen: {COST_BPS:.0f} bp round trip",
        "- no optimized thresholds; state boundaries are directional signs plus a trailing liquidity-normalized OI materiality test",
        "- direct entry and +15m transition entry are separate causal policies",
        "- mechanism diagnostic, not NautilusTrader NAV", "",
        "## State results at 4h horizon", "",
        "| state | n | direct continuation bp | PF | direct reversal bp | delayed continuation bp | delayed reversal bp |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for state in sorted(events.state.unique()):
        payload = summary["groups"].get(f"state:{state}", {})
        cont, rev = payload.get("cont_240m", {}), payload.get("rev_240m", {})
        dcont, drev = payload.get("delayed_cont_240m", {}), payload.get("delayed_rev_240m", {})
        pf = "na" if cont.get("profit_factor") is None else f"{cont['profit_factor']:.2f}"
        md.append(f"| {state} | {payload.get('events', 0)} | {10000*cont.get('mean', 0):.2f} | {pf} | {10000*rev.get('mean', 0):.2f} | {10000*dcont.get('mean', 0):.2f} | {10000*drev.get('mean', 0):.2f} |")
    md += ["", "## Global one-slot source-family results", "",
        "| family | entry | horizon | trades | trades/day | continuation bp | PF | reversal bp |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, payload in summary["global_slots"].items():
        family, entry, horizon = key.split(":")
        cont, rev = payload["continuation"], payload["reversal"]
        pf = "na" if cont.get("profit_factor") is None else f"{cont['profit_factor']:.2f}"
        md.append(f"| {family} | {entry} | {horizon} | {payload['events']} | {payload['events_per_day']:.3f} | {10000*cont.get('mean', 0):.2f} | {pf} | {10000*rev.get('mean', 0):.2f} |")
    md += ["", "## Interpretation contract", "",
        "A state is reusable only if the predicted continuation/reversal relationship appears in development, confirmation, untouched, and post-publication partitions without relying on one extreme episode. A higher aggregate total with the predicted loss group unchanged is not confirmation.", "",
        "If no state is stable, the hourly impulse family remains a statistical clue but is not promoted. If one state is stable, the next step is an executable scenario with entry, same-leg invalidation, target, one-slot arbitration, and NautilusTrader accounting; the other states become no-trade or a distinct reversal family rather than filters stacked onto one entry.", "",
    ]
    (output / "ANATOMY.md").write_text("\n".join(md))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    for name in ("start", "end", "period_label", "split", "output"):
        run.add_argument(f"--{name.replace('_', '-')}", dest=name, required=True)
    run.add_argument("--cache", default=".cache/candidate-51-derivatives-impulse-v57")
    run.add_argument("--candidate51-path", default="research/candidate-51")
    run.add_argument("--warmup-days", type=int, default=4)
    run.add_argument("--forward-days", type=int, default=1)
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
