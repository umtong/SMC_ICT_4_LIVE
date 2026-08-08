#!/usr/bin/env python3
"""Corrected prior-only beta and lead/lag diagnostics for V8 plans.

Diagnostic only. Regressors end strictly before the first response event.
The plan geometry is measured at the completed five-minute MSS bar end, not
at the following one-minute observation timestamp used to emit the plan.
"""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from math import isfinite, log, sqrt
from pathlib import Path
import re
from statistics import median
from zipfile import ZipFile

import numpy as np
import pandas as pd

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT")
COLUMNS = (
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
)
FIVE_MINUTES_NS = 5 * 60_000_000_000
HORIZONS = (24, 48, 96, 192)


def obj(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain an object")
    return payload


def load_symbol(root: Path, symbol: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in sorted((root / "data" / symbol).glob("*.zip")):
        with ZipFile(path) as archive:
            names = [name for name in archive.namelist() if not name.endswith("/")]
            if len(names) != 1:
                raise ValueError(f"{path} must contain one CSV")
            raw = archive.read(names[0])
        frame = pd.read_csv(BytesIO(raw))
        if not set(COLUMNS).issubset(frame.columns):
            frame = pd.read_csv(BytesIO(raw), header=None, names=COLUMNS)
        else:
            frame = frame.loc[:, COLUMNS]
        numeric = pd.to_numeric(frame.open_time, errors="coerce")
        frame = frame.loc[numeric.notna()].copy()
        frame.open_time = numeric[numeric.notna()].astype("int64")
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"no archives for {symbol}")
    raw = pd.concat(frames, ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
    unit = "ms" if int(raw.open_time.iloc[0]) < 10**15 else "us"
    index = pd.to_datetime(raw.open_time, unit=unit, utc=True) + pd.Timedelta(minutes=1)
    result = pd.DataFrame(index=index)
    for name in ("open", "high", "low", "close", "volume", "taker_buy_volume"):
        result[name] = pd.to_numeric(raw[name], errors="raise").to_numpy()
    return result[~result.index.duplicated(keep="last")].sort_index()


def five_minute_closes(frame: pd.DataFrame) -> pd.Series:
    close = frame.close.resample(
        "5min", closed="right", label="right", origin="epoch"
    ).last()
    count = frame.close.resample(
        "5min", closed="right", label="right", origin="epoch"
    ).count()
    return close[count == 5].dropna()


def corr(x: np.ndarray, y: np.ndarray) -> float | None:
    if len(x) < 8 or np.std(x, ddof=1) <= 0.0 or np.std(y, ddof=1) <= 0.0:
        return None
    value = float(np.corrcoef(x, y)[0, 1])
    return value if isfinite(value) else None


def fit(x: np.ndarray, y: np.ndarray) -> dict:
    x, y = np.asarray(x, float), np.asarray(y, float)
    valid = np.isfinite(x) & np.isfinite(y)
    x, y = x[valid], y[valid]
    if len(x) < 8 or np.var(x, ddof=1) <= 0.0:
        return {
            "n": len(x), "beta": None, "beta_zero_intercept": None,
            "intercept": None, "corr": None, "idio_std": None, "r2": None,
        }
    beta = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1))
    intercept = float(np.mean(y) - beta * np.mean(x))
    denom = float(np.dot(x, x))
    beta0 = float(np.dot(x, y) / denom) if denom > 0.0 else None
    residual = y - (intercept + beta * x)
    idio = float(np.std(residual, ddof=2)) if len(residual) > 2 else None
    coefficient = corr(x, y)
    return {
        "n": len(x), "beta": beta, "beta_zero_intercept": beta0,
        "intercept": intercept, "corr": coefficient, "idio_std": idio,
        "r2": None if coefficient is None else coefficient * coefficient,
    }


def lead_lag(factor: pd.Series, residual: pd.Series, horizon: int) -> dict:
    joined = pd.concat({"x": factor, "y": residual}, axis=1).dropna().iloc[-horizon:]
    output: dict[str, float | None] = {}
    for lag in range(4):
        pair = pd.concat({"x": joined.x.shift(lag), "y": joined.y}, axis=1).dropna()
        output[f"sender_lead_{lag}_corr"] = corr(pair.x.to_numpy(), pair.y.to_numpy())
        output[f"sender_lead_{lag}_beta"] = fit(
            pair.x.to_numpy(), pair.y.to_numpy()
        )["beta_zero_intercept"]
    reverse = pd.concat({"x": joined.y.shift(1), "y": joined.x}, axis=1).dropna()
    output["receiver_lead_1_corr"] = corr(reverse.x.to_numpy(), reverse.y.to_numpy())
    sender = output.get("sender_lead_1_corr")
    receiver = output.get("receiver_lead_1_corr")
    output["lead_edge"] = (
        None if sender is None or receiver is None else float(sender - receiver)
    )
    return output


def close_at(frame: pd.DataFrame, ts_ns: int) -> float | None:
    stamp = pd.Timestamp(ts_ns, unit="ns", tz="UTC")
    if stamp in frame.index:
        return float(frame.at[stamp, "close"])
    earlier = frame.loc[frame.index <= stamp, "close"]
    return float(earlier.iloc[-1]) if len(earlier) else None


def money(value: object) -> float | None:
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def first_limit_fill(events: list[dict], scenario_id: str) -> dict | None:
    start = next(
        (
            index for index, event in enumerate(events)
            if event.get("type") == "GLOBAL_ENTRY_SUBMITTED"
            and event.get("scenario_id") == scenario_id
        ),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            index for index in range(start + 1, len(events))
            if events[index].get("type") == "GLOBAL_ENTRY_SUBMITTED"
        ),
        len(events),
    )
    return next(
        (
            event for event in events[start:end]
            if event.get("type") == "ORDER_FILLED"
            and "order_type=LIMIT" in str(event.get("event", ""))
        ),
        None,
    )


def run(root: Path) -> dict:
    one = {symbol: load_symbol(root, symbol) for symbol in SYMBOLS}
    close5 = {symbol: five_minute_closes(one[symbol]) for symbol in SYMBOLS}
    returns = pd.DataFrame(
        {symbol: np.log(series / series.shift(1)) for symbol, series in close5.items()}
    ).dropna(how="all")
    plans = obj(root / "submitted_plans.json").get("plans", [])
    lifecycle = obj(root / "order_lifecycle.json").get("events", [])
    try:
        positions = pd.read_csv(root / "positions.csv")
    except pd.errors.EmptyDataError:
        positions = pd.DataFrame()
    position_map = {
        str(row["opening_order_id"]): row
        for row in positions.to_dict("records")
    } if len(positions) else {}
    pending = (
        obj(root / "pending_path_diagnostics.json")
        if (root / "pending_path_diagnostics.json").is_file()
        else {"records": []}
    )
    pending_map = {
        str(record["scenario_id"]): record
        for record in pending.get("records", [])
    }

    records: list[dict] = []
    for plan in plans:
        details = plan.get("details", {})
        transfer = details.get("candidate15_v8_transfer", {})
        accepted = tuple(str(symbol) for symbol in transfer.get("accepted_symbols", ()))
        residual_symbol = str(
            transfer.get("residual_symbol", plan.get("symbol", ""))
        )
        event_ids = tuple(
            str(item) for item in transfer.get("evidence_event_ids", ())
        )
        if len(accepted) != 3 or residual_symbol not in SYMBOLS or len(event_ids) != 2:
            continue
        first_ts = int(event_ids[0].split("-")[1])
        second_ts = int(transfer["effective_ts_ns"])
        observed_ts = int(plan["observed_ts_ns"])
        geometry_ts = int(details.get("mss_bar_end_ts_ns", observed_ts))
        if geometry_ts > observed_ts or geometry_ts <= second_ts:
            raise ValueError(
                f"invalid V8 plan geometry clock for {plan['scenario_id']}: "
                f"effective={second_ts}, geometry={geometry_ts}, observed={observed_ts}"
            )
        sign = 1.0 if plan["direction"] == "LONG" else -1.0
        history = returns.loc[
            returns.index < pd.Timestamp(first_ts, unit="ns", tz="UTC"),
            list((*accepted, residual_symbol)),
        ].dropna()
        factor = history.loc[:, list(accepted)].median(axis=1)
        residual_returns = history[residual_symbol]

        first_prices = {
            symbol: close_at(one[symbol], first_ts)
            for symbol in (*accepted, residual_symbol)
        }
        second_prices = {
            symbol: close_at(one[symbol], second_ts)
            for symbol in (*accepted, residual_symbol)
        }
        geometry_prices = {
            symbol: close_at(one[symbol], geometry_ts)
            for symbol in (*accepted, residual_symbol)
        }
        values = (
            *first_prices.values(), *second_prices.values(), *geometry_prices.values()
        )
        if any(value is None or value <= 0.0 for value in values):
            continue

        sender_state = median(
            sign * log(float(second_prices[symbol]) / float(first_prices[symbol]))
            for symbol in accepted
        )
        sender_geometry = median(
            sign * log(float(geometry_prices[symbol]) / float(first_prices[symbol]))
            for symbol in accepted
        )
        residual_state = sign * log(
            float(second_prices[residual_symbol]) /
            float(first_prices[residual_symbol])
        )
        residual_geometry = sign * log(
            float(geometry_prices[residual_symbol]) /
            float(first_prices[residual_symbol])
        )
        state_span = max(1.0, (second_ts - first_ts) / FIVE_MINUTES_NS)
        geometry_span = max(1.0, (geometry_ts - first_ts) / FIVE_MINUTES_NS)

        horizon_results: dict[str, dict] = {}
        for horizon in HORIZONS:
            joined = pd.concat({"x": factor, "y": residual_returns}, axis=1).dropna().iloc[-horizon:]
            model = fit(joined.x.to_numpy(), joined.y.to_numpy())
            beta = model["beta_zero_intercept"]
            expected_state = None if beta is None else float(beta * sender_state)
            expected_geometry = (
                None if beta is None else float(beta * sender_geometry)
            )
            idio = model["idio_std"]
            state_z = None
            geometry_z = None
            if expected_state is not None and idio is not None and idio > 0.0:
                state_z = float(
                    (residual_state - expected_state) /
                    (idio * sqrt(state_span))
                )
                geometry_z = float(
                    (residual_geometry - expected_geometry) /
                    (idio * sqrt(geometry_span))
                )
            horizon_results[str(horizon)] = {
                **model,
                "expected_state_directional_progress": expected_state,
                "expected_geometry_directional_progress": expected_geometry,
                "state_delivery_gap": (
                    None if expected_state is None
                    else float(expected_state - residual_state)
                ),
                "geometry_delivery_gap": (
                    None if expected_geometry is None
                    else float(expected_geometry - residual_geometry)
                ),
                "state_residual_z": state_z,
                "geometry_residual_z": geometry_z,
                **lead_lag(factor, residual_returns, horizon),
            }

        betas = [
            horizon_results[str(horizon)]["beta_zero_intercept"]
            for horizon in HORIZONS
        ]
        finite_betas = [
            float(value) for value in betas
            if value is not None and isfinite(float(value))
        ]
        positive_consensus = (
            len(finite_betas) == len(HORIZONS)
            and all(value > 0.0 for value in finite_betas)
        )
        beta_spread = (
            None if not finite_betas else max(finite_betas) - min(finite_betas)
        )
        fill = first_limit_fill(lifecycle, str(plan["scenario_id"]))
        position = (
            position_map.get(str(fill["client_order_id"]))
            if fill is not None else None
        )
        pnl = money(position.get("realized_pnl")) if position is not None else None
        records.append({
            "scenario_id": plan["scenario_id"],
            "symbol": plan["symbol"],
            "direction": plan["direction"],
            "stage": transfer.get("stage"),
            "accepted_symbols": list(accepted),
            "first_evidence_ts_ns": first_ts,
            "effective_ts_ns": second_ts,
            "geometry_ts_ns": geometry_ts,
            "observed_ts_ns": observed_ts,
            "confirmation_span_minutes": (
                second_ts - first_ts
            ) / 60_000_000_000,
            "state_to_geometry_minutes": (
                geometry_ts - second_ts
            ) / 60_000_000_000,
            "geometry_to_observed_minutes": (
                observed_ts - geometry_ts
            ) / 60_000_000_000,
            "sender_state_directional_progress": sender_state,
            "sender_geometry_directional_progress": sender_geometry,
            "residual_state_directional_progress": residual_state,
            "residual_geometry_directional_progress": residual_geometry,
            "v8_equal_weight_parity_price": transfer.get("parity_price"),
            "positive_beta_consensus": positive_consensus,
            "beta_spread": beta_spread,
            "horizons": horizon_results,
            "filled": fill is not None,
            "realized_pnl": pnl,
            "win": None if pnl is None else pnl > 0.0,
            "pending_first_passage": pending_map.get(
                str(plan["scenario_id"]), {}
            ).get("classification"),
        })

    output = {
        "schema": "candidate-15-prior-only-beta-transfer-diagnostics-v2",
        "diagnostic_only": True,
        "does_not_modify_execution": True,
        "estimation_cutoff": "strictly_before_first_evidence_event",
        "geometry_clock": "completed_mss_bar_end_ts_ns",
        "return_bar_minutes": 5,
        "horizons": list(HORIZONS),
        "submitted_plans": len(records),
        "filled_plans": sum(bool(record["filled"]) for record in records),
        "positive_beta_consensus_plans": sum(
            bool(record["positive_beta_consensus"]) for record in records
        ),
        "records": records,
    }
    (root / "beta_transfer_diagnostics.json").write_text(
        json.dumps(output, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(
        {key: value for key, value in output.items() if key != "records"},
        indent=2,
    ))
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    run(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
