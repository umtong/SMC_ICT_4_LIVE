#!/usr/bin/env python3
"""Exact signed-volume ablation of the frozen quarter-hour study.

The first implementation used Candidate05's existing opening signed *notional*
imbalance as a close proxy.  Kim & Hansen (2026) define OI with signed contract
quantity.  This wrapper changes only that measurement: the base study receives
opening10 total quantity and signed quantity through the same column interface.
All event clocks, trailing quantiles, horizons, 21 bp cost hurdle and the
one-position non-overlap diagnostic remain unchanged.
"""
from __future__ import annotations

import runpy
from pathlib import Path

import numpy as np
import pandas as pd

from timestamp_contract import install

install()

import features  # noqa: E402


def aggregate_open10_signed_volume(path: Path) -> pd.DataFrame:
    grouped = []
    for chunk in features._agg_reader(path):
        quantity = pd.to_numeric(chunk["quantity"], errors="raise").astype(float)
        transact = pd.to_numeric(chunk["transact_time"], errors="raise")
        unit = "us" if float(transact.iloc[0]) > 10**14 else "ms"
        timestamp = pd.to_datetime(transact, unit=unit, utc=True)
        maker = features._maker_mask(chunk["is_buyer_maker"])
        work = pd.DataFrame({
            "minute": timestamp.dt.floor("min"),
            "second": timestamp.dt.second,
            "quantity": quantity.to_numpy(),
            # is_buyer_maker=True means seller-initiated/aggressive sell.
            "signed_quantity": np.where(maker.to_numpy(), -quantity.to_numpy(), quantity.to_numpy()),
        })
        opening = work[(work["minute"].dt.minute % 15 == 0) & (work["second"] < 10)]
        if opening.empty:
            continue
        grouped.append(opening.groupby("minute", sort=True).agg(
            notional_open_10s=("quantity", "sum"),
            signed_notional_open_10s=("signed_quantity", "sum"),
            trade_count_open_10s=("quantity", "size"),
        ))
    if not grouped:
        raise RuntimeError(f"empty QH opening window: {path}")
    result = pd.concat(grouped).sort_index()
    return result.groupby(level=0, sort=True).sum()


# Patch before quarter_hour_oi_study imports the symbol from features.
features.aggregate_agg_trades = aggregate_open10_signed_volume

runpy.run_path(str(Path(__file__).with_name("quarter_hour_oi_study.py")), run_name="__main__")
