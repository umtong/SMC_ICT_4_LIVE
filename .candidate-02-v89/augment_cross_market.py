"""Augment completed futures-minute features with causal BTC spot features for v89."""
from __future__ import annotations

from hashlib import sha256
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

from v53_nt_core import load_feature_matrix, load_raw_one_minute

ROOT = Path("inputs/v89-first-week")
FEATURE_ROOT = ROOT / "candidate-02-v48-first-week"
SPOT_AGG = ROOT / "spot/aggTrades"
SPOT_KLINES = ROOT / "spot/klines"
OUT = ROOT / "artifacts/candidate-02-v89-first-week-data"
AGG_COLUMNS = [
    "agg_trade_id",
    "price",
    "quantity",
    "first_trade_id",
    "last_trade_id",
    "transact_time",
    "is_buyer_maker",
    "is_best_match",
]


def _read_archive(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if not name.endswith("/")]
        if len(members) != 1:
            raise ValueError(f"unexpected archive {path}")
        raw = archive.read(members[0])
    frame = pd.read_csv(io.BytesIO(raw))
    expected = set(AGG_COLUMNS[:-1])
    if not expected.issubset(frame.columns):
        frame = pd.read_csv(io.BytesIO(raw), header=None)
        if frame.shape[1] < 7:
            raise ValueError(f"unexpected spot aggTrade columns in {path}: {frame.shape[1]}")
        names = AGG_COLUMNS[: frame.shape[1]]
        frame.columns = names
    return frame


def _timestamp_unit(values: pd.Series) -> str:
    median = float(pd.to_numeric(values, errors="coerce").dropna().median())
    return "us" if median >= 1e14 else "ms"


def aggregate_spot_trades(path: Path) -> pd.DataFrame:
    frame = _read_archive(path)
    for column in ("price", "quantity", "transact_time"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.dropna(subset=["price", "quantity", "transact_time"], inplace=True)
    maker = frame["is_buyer_maker"]
    if maker.dtype != bool:
        maker = maker.astype(str).str.lower().map({"true": True, "false": False, "1": True, "0": False})
    if maker.isna().any():
        raise ValueError(f"invalid spot maker flags in {path}")
    price = frame["price"].to_numpy(dtype=float)
    quantity = frame["quantity"].to_numpy(dtype=float)
    quote = price * quantity
    signed = np.where(maker.to_numpy(), -quote, quote)
    timestamp = pd.to_datetime(frame["transact_time"], unit=_timestamp_unit(frame["transact_time"]), utc=True)
    minute_start = timestamp.dt.floor("min")
    minute_close = minute_start + pd.Timedelta(minutes=1)
    opening = (timestamp - minute_start).dt.total_seconds().to_numpy(dtype=float) < 10.0
    temp = pd.DataFrame({
        "minute": minute_close,
        "quote": quote,
        "signed": signed,
        "price": price,
        "opening": opening,
    })
    grouped = temp.groupby("minute", sort=True)
    result = pd.DataFrame(index=grouped.size().index)
    result["spot_aggressive_total_quote_1m"] = grouped["quote"].sum()
    result["spot_aggressive_signed_quote_1m"] = grouped["signed"].sum()
    result["spot_trade_count_1m"] = grouped.size().astype(float)

    for opening_value, prefix in ((True, "spot_qh_opening_10s"), (False, "spot_qh_rest_50s")):
        subset = temp.loc[temp["opening"] == opening_value]
        g = subset.groupby("minute", sort=True)
        total = g["quote"].sum().reindex(result.index).fillna(0.0)
        signed_sum = g["signed"].sum().reindex(result.index).fillna(0.0)
        first = g["price"].first().reindex(result.index)
        last = g["price"].last().reindex(result.index)
        result[f"{prefix}_total_quote"] = total
        result[f"{prefix}_signed_quote"] = signed_sum
        result[f"{prefix}_flow_ratio"] = (signed_sum / total.replace(0.0, np.nan)).fillna(0.0)
        result[f"{prefix}_return"] = np.log(last / first).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return result


def main() -> None:
    npz_path = FEATURE_ROOT / "v48_features.npz"
    columns_path = FEATURE_ROOT / "columns.json"
    futures = load_feature_matrix(npz_path, columns_path)
    spot_raw = load_raw_one_minute(SPOT_KLINES)
    archives = sorted(SPOT_AGG.glob("BTCUSDT-aggTrades-*.zip"))
    if len(archives) != 10:
        raise ValueError(f"expected ten spot aggTrade archives, found {len(archives)}")
    trades = pd.concat([aggregate_spot_trades(path) for path in archives]).sort_index()
    if trades.index.has_duplicates:
        raise ValueError("duplicate spot minute features")
    spot = spot_raw.rename(columns={name: f"spot_{name}" for name in ("open", "high", "low", "close", "volume")})
    data = futures.join(spot, how="left").join(trades, how="left")
    flow_columns = [
        "spot_aggressive_total_quote_1m",
        "spot_aggressive_signed_quote_1m",
        "spot_trade_count_1m",
        "spot_qh_opening_10s_total_quote",
        "spot_qh_opening_10s_signed_quote",
        "spot_qh_opening_10s_flow_ratio",
        "spot_qh_opening_10s_return",
        "spot_qh_rest_50s_total_quote",
        "spot_qh_rest_50s_signed_quote",
        "spot_qh_rest_50s_flow_ratio",
        "spot_qh_rest_50s_return",
    ]
    data[flow_columns] = data[flow_columns].fillna(0.0)
    data["spot_signed_flow_ratio_1m"] = (
        data["spot_aggressive_signed_quote_1m"]
        / data["spot_aggressive_total_quote_1m"].replace(0.0, np.nan)
    ).fillna(0.0)
    data["spot_qh_full_minute_return"] = np.log(data["spot_close"] / data["spot_open"])
    data["spot_qh_full_minute_return"] = data["spot_qh_full_minute_return"].replace([np.inf, -np.inf], np.nan)
    data["perp_spot_log_basis"] = np.log(data["close"] / data["spot_close"])
    data["perp_spot_basis_change_1m"] = data["perp_spot_log_basis"].diff()

    critical = [
        "spot_open",
        "spot_close",
        "spot_qh_opening_10s_flow_ratio",
        "spot_qh_full_minute_return",
        "perp_spot_log_basis",
    ]
    if data[critical].isna().any().any():
        gaps = {name: int(data[name].isna().sum()) for name in critical}
        raise ValueError(f"critical cross-market feature gaps: {gaps}")
    quarter_hour = ((data.index - pd.Timedelta(minutes=1)).minute % 15 == 0)
    if (data.loc[quarter_hour, "spot_aggressive_total_quote_1m"] <= 0).any():
        raise ValueError("quarter-hour event without spot trades")

    columns = [str(name) for name in data.columns]
    values = data.to_numpy(dtype=np.float64, copy=True)
    timestamps_ns = data.index.asi8.astype(np.int64, copy=True)
    temporary = npz_path.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, timestamps_ns=timestamps_ns, values=values)
    temporary.replace(npz_path)
    columns_path.write_text(json.dumps(columns, indent=2), encoding="utf-8")

    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "rows": int(len(data)),
        "first_minute_close_utc": data.index.min().isoformat(),
        "last_minute_close_utc": data.index.max().isoformat(),
        "quarter_hour_rows": int(quarter_hour.sum()),
        "columns": columns,
        "feature_npz_sha256": sha256(npz_path.read_bytes()).hexdigest(),
        "feature_npz_size": npz_path.stat().st_size,
        "causality": "spot and perpetual observations are released only at each completed minute close; all future columns are excluded",
    }
    (OUT / "cross_market_feature_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
