#!/usr/bin/env python3
"""Repair diagnostic-only timestamp and instrument identity alignment."""

from pathlib import Path

path = Path(__file__).with_name("anchor_regime_diagnostics.py")
text = path.read_text(encoding="utf-8")
replacements = {
    'values["ts_ns"] = values["close_dt"].astype("int64")':
        'values["ts_ns"] = values["close_dt"].map(lambda value: pd.Timestamp(value).value)',
    'machine = AuctionStateMachine(candidate, instrument_id="BTCUSDT-PERP.BINANCE")':
        'machine = AuctionStateMachine(candidate, instrument_id="BTCUSDT-PERP.BINANCE:240m")',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise SystemExit(f"expected one diagnostic match, found {text.count(old)}: {old}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
