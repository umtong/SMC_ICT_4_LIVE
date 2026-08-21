#!/usr/bin/env python3
"""Render every candidate trade as dependency-free SVG for one-by-one diagnosis."""
from __future__ import annotations

import argparse
from datetime import date, timedelta
from html import escape
from pathlib import Path
import math

import numpy as np
import pandas as pd

from data_re1_flow import load_range_flow

W, H = 1600, 1050
LEFT, RIGHT = 85, 45
PLOT_W = W - LEFT - RIGHT
CONTEXT_Y, CONTEXT_H = 95, 300
DETAIL_Y, DETAIL_H = 430, 410
FLOW_Y, FLOW_H = 875, 125
UP, DOWN = "#15826f", "#c44949"
GRID, TEXT = "#d7dce2", "#18212b"


def _finite(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _ts(value):
    return pd.Timestamp(int(value), unit="ns", tz="UTC")


def _x(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> float:
    if len(index) <= 1:
        return LEFT
    pos = int(index.searchsorted(timestamp, side="left"))
    pos = min(max(pos, 0), len(index) - 1)
    return LEFT + pos / max(len(index) - 1, 1) * PLOT_W


def _yscale(low: float, high: float, y: float, height: float):
    span = max(high - low, 1e-12)
    return lambda value: y + height - (float(value) - low) / span * height


def _candles(parts: list[str], frame: pd.DataFrame, y: float, height: float, extra_prices=()):
    if frame.empty:
        return None
    valid = [float(value) for value in extra_prices if math.isfinite(float(value))]
    low = min([float(frame.low.min()), *valid])
    high = max([float(frame.high.max()), *valid])
    pad = max((high - low) * 0.05, abs(high) * 1e-6, 1e-12)
    low, high = low - pad, high + pad
    sy = _yscale(low, high, y, height)
    n = len(frame)
    width = max(1.0, min(8.0, PLOT_W / max(n, 1) * 0.68))
    for i, row in enumerate(frame.itertuples()):
        x = LEFT + (i + 0.5) / n * PLOT_W
        color = UP if row.close >= row.open else DOWN
        parts.append(f'<line x1="{x:.2f}" y1="{sy(row.low):.2f}" x2="{x:.2f}" y2="{sy(row.high):.2f}" stroke="{color}" stroke-width="1"/>')
        top, bottom = sy(max(row.open, row.close)), sy(min(row.open, row.close))
        parts.append(f'<rect x="{x-width/2:.2f}" y="{top:.2f}" width="{width:.2f}" height="{max(1.0,bottom-top):.2f}" fill="{color}"/>')
    for fraction in (0, .25, .5, .75, 1):
        py = y + height * (1 - fraction)
        price = low + (high - low) * fraction
        parts.append(f'<line x1="{LEFT}" y1="{py:.1f}" x2="{W-RIGHT}" y2="{py:.1f}" stroke="{GRID}" stroke-width="0.7"/>')
        parts.append(f'<text x="8" y="{py+4:.1f}" font-size="12" fill="{TEXT}">{price:.8g}</text>')
    return sy


def _hline(parts, sy, price, label, color, dash="", width=1.4):
    py = sy(price)
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    parts.append(f'<line x1="{LEFT}" y1="{py:.2f}" x2="{W-RIGHT}" y2="{py:.2f}" stroke="{color}" stroke-width="{width}"{dash_attr}/>')
    parts.append(f'<text x="{W-RIGHT-5}" y="{py-4:.2f}" font-size="12" text-anchor="end" fill="{color}">{escape(label)}</text>')


def _band(parts, sy, lower, upper, label, color, opacity=.12):
    y1, y2 = sy(upper), sy(lower)
    parts.append(f'<rect x="{LEFT}" y="{min(y1,y2):.2f}" width="{PLOT_W}" height="{max(1,abs(y2-y1)):.2f}" fill="{color}" opacity="{opacity}"/>')
    parts.append(f'<text x="{LEFT+5}" y="{min(y1,y2)+14:.2f}" font-size="12" fill="{color}">{escape(label)}</text>')


def _vline(parts, index, timestamp, y, height, label, color):
    x = _x(index, timestamp)
    parts.append(f'<line x1="{x:.2f}" y1="{y}" x2="{x:.2f}" y2="{y+height}" stroke="{color}" stroke-width="1.1" stroke-dasharray="4 4"/>')
    parts.append(f'<text x="{x+3:.2f}" y="{y+14}" font-size="11" fill="{color}" transform="rotate(90 {x+3:.2f} {y+14})">{escape(label)}</text>')


def _flow_panel(parts, frame: pd.DataFrame):
    if frame.empty:
        return
    quote = frame.quote_volume.to_numpy(float)
    delta = (2 * frame.taker_buy_quote_volume - frame.quote_volume).to_numpy(float)
    scale = max(float(np.nanpercentile(quote, 99)), 1e-12)
    delta_scale = max(float(np.nanpercentile(np.abs(delta), 99)), 1e-12)
    n = len(frame)
    width = max(.8, PLOT_W / max(n, 1) * .72)
    middle = FLOW_Y + FLOW_H * .62
    for i, (volume, signed) in enumerate(zip(quote, delta)):
        x = LEFT + (i + .5) / n * PLOT_W
        volume_height = min(volume / scale, 1.2) * FLOW_H * .52
        parts.append(f'<rect x="{x-width/2:.2f}" y="{middle-volume_height:.2f}" width="{width:.2f}" height="{volume_height:.2f}" fill="#7d8793" opacity=".48"/>')
        delta_height = max(-1.2, min(1.2, signed / delta_scale)) * FLOW_H * .32
        color = UP if signed >= 0 else DOWN
        zero = middle + FLOW_H * .30
        parts.append(f'<line x1="{x:.2f}" y1="{zero:.2f}" x2="{x:.2f}" y2="{zero-delta_height:.2f}" stroke="{color}" stroke-width="{max(1,width*.6):.2f}"/>')
    parts.append(f'<line x1="{LEFT}" y1="{middle+FLOW_H*.30:.1f}" x2="{W-RIGHT}" y2="{middle+FLOW_H*.30:.1f}" stroke="{GRID}"/>')
    parts.append(f'<text x="8" y="{FLOW_Y+18}" font-size="12" fill="{TEXT}">quote volume (gray) / aggressor delta (green-red)</text>')


def render_case(row: pd.Series, raw: pd.DataFrame, path: Path):
    indexed = raw.copy()
    indexed.index = pd.DatetimeIndex(indexed.pop("open_time_dt")) + pd.Timedelta(minutes=1)
    interaction, emission, fill = _ts(row.interaction_time_ns), _ts(row.emission_time_ns), _ts(row.fill_time_ns)
    resolution = _ts(row.resolution_time_ns) if pd.notna(row.resolution_time_ns) else fill + pd.Timedelta(minutes=1)
    event = _ts(row.diagnostic_event_time_ns)
    displacement = _ts(row.diagnostic_displacement_time_ns)
    first_return = _ts(row.diagnostic_first_return_time_ns)
    response = _ts(row.diagnostic_response_time_ns)
    context = indexed.loc[interaction-pd.Timedelta(hours=12):resolution+pd.Timedelta(hours=2)].copy()
    context15 = context.resample("15min", label="right", closed="right").agg(
        open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"),
        quote_volume=("quote_volume","sum"), taker_buy_quote_volume=("taker_buy_quote_volume","sum"),
    ).dropna()
    detail = indexed.loc[interaction-pd.Timedelta(minutes=90):resolution+pd.Timedelta(minutes=35)].copy()
    prices = [row.source_lower,row.source_upper,row.diagnostic_origin_lower,row.diagnostic_origin_upper,row.entry,row.stop,row.target,row.diagnostic_event_extreme,row.diagnostic_retest_extreme]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">', '<rect width="100%" height="100%" fill="#fbfcfe"/>']
    account_r = row.target_net_r / abs(row.stop_net_r) if row.outcome == "TARGET_FIRST" else -1.0
    title = (f"{row.symbol} | {emission.isoformat()} | {row.event_type} {row.side} | {row.origin_kind}/{row.response_kind} | "
             f"{row.outcome} | account {account_r:+.2f}R | gross RR {row.gross_rr:.2f} | hold {int(_finite(row.holding_minutes,0))}m")
    parts.append(f'<text x="{LEFT}" y="32" font-size="20" font-weight="700" fill="{TEXT}">{escape(title)}</text>')
    meta = (f"source {int(row.source_timeframe_minutes)}m {row.source_kind} age {row.source_age_minutes:.0f}m; objective {row.objective_kind}; "
            f"penetration {row.event_penetration_bps:.1f}bps; event→displacement {row.event_to_displacement_minutes:.0f}m; "
            f"wait {row.return_wait_minutes:.0f}m; response {row.response_delay_minutes:.0f}m; risk {row.risk_bps:.1f}bps")
    parts.append(f'<text x="{LEFT}" y="57" font-size="13" fill="#485564">{escape(meta)}</text>')
    sy1 = _candles(parts, context15, CONTEXT_Y, CONTEXT_H, prices)
    sy2 = _candles(parts, detail, DETAIL_Y, DETAIL_H, prices)
    for sy in (sy1, sy2):
        if sy is None:
            continue
        _band(parts, sy, row.source_lower, row.source_upper, "pre-existing liquidity/structure", "#7d52a8", .10)
        _band(parts, sy, row.diagnostic_origin_lower, row.diagnostic_origin_upper, "fresh displacement origin", "#d29d23", .15)
        _hline(parts, sy, row.entry, "signal close / market entry next minute", "#2563a5")
        _hline(parts, sy, row.stop, "invalidation / stop", "#b72e35", "5 3")
        _hline(parts, sy, row.target, "nearest pre-existing objective", "#25834f", "5 3")
        _hline(parts, sy, row.diagnostic_event_extreme, "event extreme", "#954b8d", "2 4", 1.0)
        _hline(parts, sy, row.diagnostic_retest_extreme, "retest extreme", "#9a6c19", "2 4", 1.0)
    for idx, y, height in ((context15.index,CONTEXT_Y,CONTEXT_H),(detail.index,DETAIL_Y,DETAIL_H)):
        for timestamp, label, color in ((interaction,"interaction","#6b7280"),(event,"event","#7d52a8"),(displacement,"BOS/displacement","#d07a00"),(first_return,"first return","#9a6c19"),(response,"response","#2563a5"),(fill,"fill","#1e3a8a"),(resolution,"exit","#111827")):
            _vline(parts, idx, timestamp, y, height, label, color)
    _flow_panel(parts, detail)
    if len(detail):
        for i in np.linspace(0,len(detail)-1,min(9,len(detail))).astype(int):
            x = LEFT + (i + .5) / len(detail) * PLOT_W
            parts.append(f'<text x="{x:.1f}" y="{H-18}" font-size="11" text-anchor="middle" fill="{TEXT}">{detail.index[i].strftime("%m-%d %H:%M")}</text>')
    parts.append('</svg>')
    path.write_text('\n'.join(parts), encoding='utf-8')
    return account_r


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', type=date.fromisoformat, required=True)
    parser.add_argument('--end', type=date.fromisoformat, required=True)
    parser.add_argument('--warmup-days', type=int, default=20)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--actions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    actions = pd.read_csv(args.actions).sort_values(['emission_time_ns','symbol']).reset_index(drop=True)
    required = {'diagnostic_event_time_ns','diagnostic_displacement_time_ns','diagnostic_origin_lower','diagnostic_origin_upper','diagnostic_first_return_time_ns','diagnostic_response_time_ns','diagnostic_retest_extreme'}
    missing = required - set(actions.columns)
    if missing:
        raise RuntimeError(f'missing diagnostic geometry {sorted(missing)}')
    manifest, links = [], []
    for symbol, group in actions.groupby('symbol'):
        raw = load_range_flow(symbol, args.start-timedelta(days=args.warmup_days), args.end+timedelta(days=1), args.cache)
        for rank, (_, row) in enumerate(group.iterrows(), start=1):
            stamp = _ts(row.emission_time_ns).strftime('%Y%m%d_%H%M')
            name = f"{symbol}_{stamp}_{row.event_type}_{row.side}_{row.outcome}_{rank:03d}.svg"
            account_r = render_case(row, raw, args.output/name)
            manifest.append({**row.to_dict(), 'account_r': account_r, 'chart': name})
            links.append((name,row,account_r))
    pd.DataFrame(manifest).to_csv(args.output/'cases_manifest.csv', index=False)
    html = ['<!doctype html><meta charset="utf-8"><title>Liquidity displacement cases</title>',
            '<style>body{font:14px sans-serif;background:#f6f7f9}a{display:block;margin:9px;padding:10px;background:white;text-decoration:none;color:#17202a;border:1px solid #ddd}</style>',
            '<h1>Every causal liquidity-displacement trade</h1>']
    for name, row, account_r in sorted(links, key=lambda item: int(item[1].emission_time_ns)):
        html.append(f'<a href="{escape(name)}">{escape(row.symbol)} {escape(str(_ts(row.emission_time_ns)))} {escape(row.event_type)} {escape(row.side)} {escape(row.outcome)} {account_r:+.2f}R</a>')
    (args.output/'index.html').write_text('\n'.join(html), encoding='utf-8')
    print({'charts': len(manifest), 'output': str(args.output)})


if __name__ == '__main__':
    main()
