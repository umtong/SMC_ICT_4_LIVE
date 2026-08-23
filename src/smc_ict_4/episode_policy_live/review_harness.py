"""Render source-bound trade and terminal no-trade episode reviews.

This is an offline research tool.  It never writes policy state and its
counterfactual outcome is deliberately labeled ``OFFLINE_AUDIT_ONLY``.

Renderer provenance
-------------------
The SVG composition is adapted from the already-existing branch renderer:

* ``bf5ef43ec5e540b0f7dbd9f1fdf7b5f2516a20db``
  ``research/candidate-coherent-liquidity-policy-v1/render_selected.py``
* schema-adapter precedent ``d19bad24566274979ba12d526e9bc858e0a5d1ed``
  ``research/candidate-hierarchical-liquidity-bpr-v1/render_cases.py``

No detector or policy rule is reconstructed here.  Every overlay comes from
``episode_decisions.csv`` / ``trades.csv`` and every candle comes from the
checksum-verified trade archives recorded in the replay ``run.json``.
"""
from __future__ import annotations

import argparse
import bisect
import csv
from dataclasses import dataclass
from hashlib import sha256
from html import escape
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .domain import SYMBOLS
from .nautilus_data import BinanceKline1mLoader


MINUTE_NS = 60_000_000_000
HOUR_NS = 60 * MINUTE_NS
W, H = 1680, 1120
LEFT, RIGHT = 90, 55
PLOT_W = W - LEFT - RIGHT
CONTEXT_Y, CONTEXT_H = 105, 320
DETAIL_Y, DETAIL_H = 465, 420
FLOW_Y, FLOW_H = 920, 135
UP, DOWN = "#16836d", "#c34a4a"
GRID, TEXT = "#d8dde3", "#17212c"
PROVENANCE = (
    {
        "commit": "bf5ef43ec5e540b0f7dbd9f1fdf7b5f2516a20db",
        "path": "research/candidate-coherent-liquidity-policy-v1/render_selected.py",
        "role": "dependency-free SVG trade renderer",
    },
    {
        "commit": "d19bad24566274979ba12d526e9bc858e0a5d1ed",
        "path": "research/candidate-hierarchical-liquidity-bpr-v1/render_cases.py",
        "role": "explicit evidence-schema adapter precedent",
    },
)


class ReviewHarnessError(RuntimeError):
    """The replay cannot support an honest chart review."""


@dataclass(frozen=True, slots=True)
class ReviewInputs:
    run_dir: Path
    run_path: Path
    trades_path: Path
    decisions_path: Path
    run: Mapping[str, Any]
    sources: Mapping[str, tuple[Path, ...]]
    source_records: tuple[Mapping[str, Any], ...]


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_object(value: Any) -> dict[str, Any]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ReviewHarnessError("malformed JSON evidence in episode decision") from exc
    if not isinstance(parsed, Mapping):
        raise ReviewHarnessError("episode decision JSON field is not an object")
    return dict(parsed)


def _optional_float(value: Any) -> float | None:
    if value is None or str(value) in {"", "nan", "None", "<NA>"}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    number = _optional_float(value)
    return None if number is None else int(number)


def _native_float(value: Any) -> float:
    converter = getattr(value, "as_double", None)
    return float(converter()) if callable(converter) else float(value)


def _resolve_path(value: Any, run_dir: Path) -> Path:
    if not isinstance(value, str) or not value:
        raise ReviewHarnessError("run.json contains an invalid trade archive path")
    path = Path(value)
    return path if path.is_absolute() else (run_dir / path).resolve()


def _source_integrity_records(run: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    integrity = run.get("source_integrity")
    if not isinstance(integrity, Mapping) or integrity.get("all_verified") is not True:
        raise ReviewHarnessError(
            "run.json does not attest checksum-verified replay sources",
        )
    archives = integrity.get("archives")
    trades = archives.get("trade_klines") if isinstance(archives, Mapping) else None
    if not isinstance(trades, Mapping):
        raise ReviewHarnessError("run.json has no verified trade-kline manifest")
    records: dict[str, dict[str, Any]] = {}
    for symbol in SYMBOLS:
        items = trades.get(symbol)
        if not isinstance(items, list) or not items:
            raise ReviewHarnessError(f"verified trade-kline manifest missing {symbol}")
        for item in items:
            if not isinstance(item, Mapping):
                raise ReviewHarnessError("malformed trade-kline integrity record")
            if item.get("checksum_verified") is not True:
                raise ReviewHarnessError(f"unverified trade archive: {item.get('path')}")
            path = item.get("path")
            digest = item.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64:
                raise ReviewHarnessError("trade-kline integrity record lacks path/SHA-256")
            records[str(Path(path).resolve()).casefold()] = dict(item)
    return records


def load_review_inputs(run_dir: str | Path) -> ReviewInputs:
    root = Path(run_dir).resolve()
    run_path = root / "run.json"
    trades_path = root / "trades.csv"
    decisions_path = root / "episode_decisions.csv"
    for path, label in (
        (run_path, "run.json"),
        (trades_path, "trades.csv"),
        (decisions_path, "episode_decisions.csv"),
    ):
        if not path.is_file():
            raise ReviewHarnessError(
                f"review input missing {label}: {path}; rerun replay with the decision ledger",
            )
    try:
        run = json.loads(run_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewHarnessError(f"cannot read replay run.json: {run_path}") from exc
    if not isinstance(run, Mapping):
        raise ReviewHarnessError("run.json root is not an object")
    manifest = _source_integrity_records(run)
    sources_root = run.get("sources")
    trade_sources = (
        sources_root.get("trade_klines")
        if isinstance(sources_root, Mapping)
        else None
    )
    if not isinstance(trade_sources, Mapping) or set(trade_sources) != set(SYMBOLS):
        raise ReviewHarnessError("run.json trade sources do not contain the four markets")
    sources: dict[str, tuple[Path, ...]] = {}
    used_records: list[Mapping[str, Any]] = []
    for symbol in SYMBOLS:
        values = trade_sources[symbol]
        if not isinstance(values, list) or not values:
            raise ReviewHarnessError(f"run.json trade sources missing {symbol}")
        resolved = tuple(_resolve_path(value, root) for value in values)
        for path in resolved:
            if not path.is_file():
                raise ReviewHarnessError(f"trade archive is unavailable: {path}")
            record = manifest.get(str(path.resolve()).casefold())
            if record is None:
                raise ReviewHarnessError(
                    f"trade archive is not bound to the verified manifest: {path}",
                )
            expected_bytes = _optional_int(record.get("bytes"))
            if expected_bytes is not None and path.stat().st_size != expected_bytes:
                raise ReviewHarnessError(f"trade archive byte size changed: {path}")
            used_records.append(record)
        sources[symbol] = resolved
    return ReviewInputs(
        run_dir=root,
        run_path=run_path,
        trades_path=trades_path,
        decisions_path=decisions_path,
        run=run,
        sources=sources,
        source_records=tuple(used_records),
    )


def _require_columns(frame: pd.DataFrame, names: Iterable[str], label: str) -> None:
    missing = sorted(set(names) - set(frame.columns))
    if missing:
        raise ReviewHarnessError(f"{label} missing required columns: {missing}")


def _stable_case_key(row: Mapping[str, Any]) -> str:
    return sha256(
        f"{row.get('episode_id')}|{row.get('terminal_reason')}|"
        f"{row.get('family')}|{row.get('symbol')}".encode("utf-8"),
    ).hexdigest()


def select_terminal_no_trades(
    decisions: Sequence[Mapping[str, Any]],
    *,
    per_group: int,
    include_all: bool,
) -> list[dict[str, Any]]:
    if per_group < 1:
        raise ValueError("per_group must be positive")
    eligible = [
        dict(row)
        for row in decisions
        if str(row.get("episode_status")) == "TERMINAL"
        and str(row.get("outcome")) == "NO_TRADE"
    ]
    if include_all:
        return sorted(
            eligible,
            key=lambda row: (
                _optional_int(row.get("interaction_time_ns")) or 0,
                str(row.get("episode_id")),
            ),
        )
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in eligible:
        key = (
            str(row.get("terminal_reason") or "UNKNOWN"),
            str(row.get("family") or "UNKNOWN"),
            str(row.get("symbol") or "UNKNOWN"),
        )
        groups.setdefault(key, []).append(row)
    selected: list[dict[str, Any]] = []
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda row: (_stable_case_key(row), str(row.get("episode_id"))))
        selected.extend(ordered[:per_group])
    return sorted(
        selected,
        key=lambda row: (
            _optional_int(row.get("interaction_time_ns")) or 0,
            str(row.get("episode_id")),
        ),
    )


def _unique_trade_decision(
    trade: Mapping[str, Any],
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    trade_id = trade.get("trade_id")
    if trade_id not in {None, "", "nan"}:
        exact = [row for row in decisions if str(row.get("trade_id")) == str(trade_id)]
        if len(exact) == 1:
            return dict(exact[0])
        if len(exact) > 1:
            raise ReviewHarnessError(f"multiple decision rows for trade_id={trade_id}")
    episode_id, plan_id = trade.get("episode_id"), trade.get("plan_id")
    exact = [
        row for row in decisions
        if str(row.get("episode_id")) == str(episode_id)
        and str(row.get("plan_id")) == str(plan_id)
    ]
    if len(exact) != 1:
        raise ReviewHarnessError(
            f"actual trade lacks one exact episode/plan decision: "
            f"trade_id={trade_id}, episode_id={episode_id}, plan_id={plan_id}",
        )
    return dict(exact[0])


def build_review_cases(
    trades: Sequence[Mapping[str, Any]],
    decisions: Sequence[Mapping[str, Any]],
    *,
    no_trade_per_group: int,
    all_no_trades: bool,
) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    traded_episodes: set[str] = set()
    for trade in trades:
        decision = _unique_trade_decision(trade, decisions)
        episode_id = str(decision.get("episode_id"))
        traded_episodes.add(episode_id)
        case = dict(decision)
        case.update({f"actual_{key}": value for key, value in trade.items()})
        case["review_case_kind"] = "ACTUAL_TRADE"
        case["offline_future_outcome"] = "NOT_APPLICABLE_ACTUAL_TRADE"
        case["offline_future_outcome_basis"] = "NATIVE_ACTUAL_TRADE_LEDGER"
        cases.append(case)
    no_trades = select_terminal_no_trades(
        decisions,
        per_group=no_trade_per_group,
        include_all=all_no_trades,
    )
    for decision in no_trades:
        if str(decision.get("episode_id")) in traded_episodes:
            raise ReviewHarnessError("episode is both an actual trade and terminal no-trade")
        case = dict(decision)
        case["review_case_kind"] = "TERMINAL_NO_TRADE"
        cases.append(case)
    cases.sort(
        key=lambda row: (
            _optional_int(row.get("interaction_time_ns")) or 0,
            0 if row["review_case_kind"] == "ACTUAL_TRADE" else 1,
            str(row.get("episode_id")),
        ),
    )
    return cases


def _case_evidence(case: Mapping[str, Any]) -> dict[str, Any]:
    return _json_object(case.get("evidence_json"))


def _case_plan(case: Mapping[str, Any]) -> dict[str, Any]:
    return _json_object(case.get("plan_json"))


def _case_value(case: Mapping[str, Any], name: str) -> Any:
    value = case.get(name)
    if value is not None and str(value) not in {"", "nan", "None", "<NA>"}:
        return value
    evidence = _case_evidence(case)
    if name in evidence:
        return evidence[name]
    plan = _case_plan(case)
    if name in plan:
        return plan[name]
    return None


def _entry_zone(case: Mapping[str, Any]) -> tuple[float, float, str] | None:
    plan = _case_plan(case)
    zone = plan.get("entry_zone") if isinstance(plan, Mapping) else None
    if not isinstance(zone, Mapping):
        return None
    lower, upper = _optional_float(zone.get("lower")), _optional_float(zone.get("upper"))
    if lower is None or upper is None or lower >= upper:
        return None
    return lower, upper, str(zone.get("kind") or "entry zone")


def _case_window(case: Mapping[str, Any], horizon_minutes: int) -> tuple[int, int]:
    interaction = _optional_int(case.get("interaction_time_ns"))
    if interaction is None:
        raise ReviewHarnessError(f"episode {case.get('episode_id')} lacks interaction_time_ns")
    start = interaction - 12 * HOUR_NS
    if case.get("review_case_kind") == "ACTUAL_TRADE":
        end = _optional_int(case.get("actual_exit_time_ns"))
        if end is None:
            raise ReviewHarnessError(f"actual trade {case.get('actual_trade_id')} lacks exit_time_ns")
    else:
        terminal = _optional_int(case.get("terminal_time_ns"))
        if terminal is None:
            raise ReviewHarnessError(f"terminal no-trade {case.get('episode_id')} lacks terminal_time_ns")
        end = terminal + horizon_minutes * MINUTE_NS
    return start, end + 2 * HOUR_NS


def _merge_intervals(values: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    output: list[list[int]] = []
    for start, end in sorted(values):
        if not output or start > output[-1][1] + MINUTE_NS:
            output.append([start, end])
        else:
            output[-1][1] = max(output[-1][1], end)
    return [(start, end) for start, end in output]


def load_case_bars(
    sources: Mapping[str, tuple[Path, ...]],
    cases: Sequence[Mapping[str, Any]],
    *,
    horizon_minutes: int,
) -> dict[str, pd.DataFrame]:
    intervals: dict[str, list[tuple[int, int]]] = {symbol: [] for symbol in SYMBOLS}
    for case in cases:
        symbol = str(case.get("symbol"))
        if symbol not in intervals:
            raise ReviewHarnessError(f"unsupported case symbol: {symbol}")
        intervals[symbol].append(_case_window(case, horizon_minutes))
    intervals = {symbol: _merge_intervals(values) for symbol, values in intervals.items()}
    starts = {symbol: [item[0] for item in values] for symbol, values in intervals.items()}
    rows: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in SYMBOLS}

    def retained(symbol: str, time_ns: int) -> bool:
        values = intervals[symbol]
        if not values:
            return False
        position = bisect.bisect_right(starts[symbol], time_ns) - 1
        return position >= 0 and time_ns <= values[position][1]

    for minute in BinanceKline1mLoader(sources):
        for symbol in SYMBOLS:
            if not retained(symbol, minute.ts_event):
                continue
            bar = minute.bars[symbol]
            flow = minute.flows[symbol]
            rows[symbol].append(
                {
                    "time_ns": minute.ts_event,
                    "open": _native_float(bar.open),
                    "high": _native_float(bar.high),
                    "low": _native_float(bar.low),
                    "close": _native_float(bar.close),
                    "quote_volume": float(flow.quote_volume),
                    "taker_buy_quote_volume": float(flow.taker_buy_quote_volume),
                    "signed_quote_flow": float(flow.signed_quote_flow),
                },
            )
    output: dict[str, pd.DataFrame] = {}
    for symbol, values in rows.items():
        frame = pd.DataFrame(values)
        if frame.empty:
            output[symbol] = frame
            continue
        frame.index = pd.to_datetime(frame.pop("time_ns"), unit="ns", utc=True)
        if frame.index.duplicated().any() or not frame.index.is_monotonic_increasing:
            raise ReviewHarnessError(f"non-unique/non-monotonic chart bars for {symbol}")
        output[symbol] = frame
    return output


def evaluate_offline_no_trade(
    case: Mapping[str, Any],
    raw: pd.DataFrame,
    *,
    horizon_minutes: int,
) -> tuple[str, str, int | None]:
    """Evaluate geometry after a terminal no-trade, never policy evidence."""

    entry = _optional_float(_case_value(case, "entry"))
    stop = _optional_float(_case_value(case, "stop"))
    target = _optional_float(_case_value(case, "target"))
    side = str(case.get("side") or "")
    terminal = _optional_int(case.get("terminal_time_ns"))
    basis = "OFFLINE_AUDIT_ONLY;STOP_FIRST_ON_SAME_BAR;NOT_POLICY_EVIDENCE"
    if (
        entry is None or stop is None or target is None or terminal is None
        or side not in {"LONG", "SHORT"}
        or (side == "LONG" and not stop < entry < target)
        or (side == "SHORT" and not target < entry < stop)
    ):
        return "NOT_EVALUABLE_MISSING_GEOMETRY", basis, None
    start = pd.Timestamp(terminal, unit="ns", tz="UTC")
    end = start + pd.Timedelta(minutes=horizon_minutes)
    future = raw[(raw.index > start) & (raw.index <= end)]
    if future.empty:
        return "UNRESOLVED_NO_FUTURE_BARS", basis, None
    instruction = str(_case_value(case, "entry_execution_instruction") or "")
    filled = instruction == "IMMEDIATE_MARKETABLE_FIRST_RESPONSE"
    fill_time = terminal if filled else None
    for timestamp, bar in future.iterrows():
        entry_hit = float(bar.low) <= entry <= float(bar.high)
        stop_hit = float(bar.low) <= stop if side == "LONG" else float(bar.high) >= stop
        target_hit = float(bar.high) >= target if side == "LONG" else float(bar.low) <= target
        if not filled:
            if entry_hit:
                filled = True
                fill_time = int(timestamp.value)
            elif target_hit:
                return "TARGET_SPENT_BEFORE_ENTRY", basis, int(timestamp.value)
            elif stop_hit:
                return "STOP_INVALIDATED_BEFORE_ENTRY", basis, int(timestamp.value)
            else:
                continue
        if stop_hit:
            return "STOP_FIRST", basis, int(timestamp.value)
        if target_hit:
            return "TARGET_FIRST", basis, int(timestamp.value)
    if filled:
        return "OPEN_AT_HORIZON", basis, fill_time
    return "UNFILLED_AT_HORIZON", basis, None


def _ts(value: int) -> pd.Timestamp:
    return pd.Timestamp(int(value), unit="ns", tz="UTC")


def _x(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> float:
    if len(index) <= 1:
        return LEFT
    position = int(index.searchsorted(timestamp, side="left"))
    position = min(max(position, 0), len(index) - 1)
    return LEFT + position / max(len(index) - 1, 1) * PLOT_W


def _yscale(low: float, high: float, y: float, height: float):
    span = max(high - low, 1e-12)
    return lambda value: y + height - (float(value) - low) / span * height


def _candles(
    parts: list[str],
    frame: pd.DataFrame,
    y: float,
    height: float,
    extra_prices: Iterable[float],
):
    if frame.empty:
        return None
    valid = [value for value in extra_prices if math.isfinite(value)]
    low = min([float(frame.low.min()), *valid])
    high = max([float(frame.high.max()), *valid])
    pad = max((high - low) * 0.055, abs(high) * 1e-6, 1e-12)
    low, high = low - pad, high + pad
    sy = _yscale(low, high, y, height)
    count = len(frame)
    width = max(1.0, min(8.0, PLOT_W / max(count, 1) * 0.68))
    for index, row in enumerate(frame.itertuples()):
        x = LEFT + (index + 0.5) / count * PLOT_W
        color = UP if row.close >= row.open else DOWN
        parts.append(
            f'<line x1="{x:.2f}" y1="{sy(row.low):.2f}" x2="{x:.2f}" '
            f'y2="{sy(row.high):.2f}" stroke="{color}" stroke-width="1"/>',
        )
        top, bottom = sy(max(row.open, row.close)), sy(min(row.open, row.close))
        parts.append(
            f'<rect x="{x-width/2:.2f}" y="{top:.2f}" width="{width:.2f}" '
            f'height="{max(1.0, bottom-top):.2f}" fill="{color}"/>',
        )
    for fraction in (0, 0.25, 0.5, 0.75, 1):
        py = y + height * (1 - fraction)
        price = low + (high - low) * fraction
        parts.append(
            f'<line x1="{LEFT}" y1="{py:.1f}" x2="{W-RIGHT}" y2="{py:.1f}" '
            f'stroke="{GRID}" stroke-width=".7"/>',
        )
        parts.append(
            f'<text x="8" y="{py+4:.1f}" font-size="12" fill="{TEXT}">{price:.8g}</text>',
        )
    return sy


def _hline(parts: list[str], sy: Any, price: float, label: str, color: str, dash: str = "") -> None:
    y = sy(price)
    dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
    parts.append(
        f'<line x1="{LEFT}" y1="{y:.2f}" x2="{W-RIGHT}" y2="{y:.2f}" '
        f'stroke="{color}" stroke-width="1.4"{dash_attr}/>',
    )
    parts.append(
        f'<text x="{W-RIGHT-5}" y="{y-4:.2f}" font-size="12" text-anchor="end" '
        f'fill="{color}">{escape(label)}</text>',
    )


def _band(parts: list[str], sy: Any, lower: float, upper: float, label: str, color: str) -> None:
    y1, y2 = sy(upper), sy(lower)
    parts.append(
        f'<rect x="{LEFT}" y="{min(y1,y2):.2f}" width="{PLOT_W}" '
        f'height="{max(1.0,abs(y2-y1)):.2f}" fill="{color}" opacity=".12"/>',
    )
    parts.append(
        f'<text x="{LEFT+5}" y="{min(y1,y2)+14:.2f}" font-size="12" '
        f'fill="{color}">{escape(label)}</text>',
    )


def _vline(
    parts: list[str],
    index: pd.DatetimeIndex,
    timestamp: pd.Timestamp,
    y: float,
    height: float,
    label: str,
    color: str,
) -> None:
    x = _x(index, timestamp)
    parts.append(
        f'<line x1="{x:.2f}" y1="{y}" x2="{x:.2f}" y2="{y+height}" '
        f'stroke="{color}" stroke-width="1.1" stroke-dasharray="4 4"/>',
    )
    parts.append(
        f'<text x="{x+3:.2f}" y="{y+14}" font-size="11" fill="{color}" '
        f'transform="rotate(90 {x+3:.2f} {y+14})">{escape(label)}</text>',
    )


def _flow(parts: list[str], frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    quote = frame.quote_volume.to_numpy(float)
    delta = frame.signed_quote_flow.to_numpy(float)
    qscale = max(float(np.nanpercentile(quote, 99)), 1e-12)
    dscale = max(float(np.nanpercentile(np.abs(delta), 99)), 1e-12)
    count = len(frame)
    width = max(0.8, PLOT_W / max(count, 1) * 0.72)
    middle, zero = FLOW_Y + FLOW_H * 0.58, FLOW_Y + FLOW_H * 0.82
    for index, (volume, signed) in enumerate(zip(quote, delta, strict=True)):
        x = LEFT + (index + 0.5) / count * PLOT_W
        volume_height = min(volume / qscale, 1.2) * FLOW_H * 0.48
        parts.append(
            f'<rect x="{x-width/2:.2f}" y="{middle-volume_height:.2f}" '
            f'width="{width:.2f}" height="{volume_height:.2f}" fill="#7b8794" opacity=".45"/>',
        )
        delta_height = max(-1.2, min(1.2, signed / dscale)) * FLOW_H * 0.28
        color = UP if signed >= 0 else DOWN
        parts.append(
            f'<line x1="{x:.2f}" y1="{zero:.2f}" x2="{x:.2f}" '
            f'y2="{zero-delta_height:.2f}" stroke="{color}" '
            f'stroke-width="{max(1.0,width*.6):.2f}"/>',
        )
    parts.append(f'<line x1="{LEFT}" y1="{zero}" x2="{W-RIGHT}" y2="{zero}" stroke="{GRID}"/>')
    parts.append(f'<text x="8" y="{FLOW_Y+16}" font-size="12" fill="{TEXT}">quote volume / aggressor delta</text>')


def _case_markers(case: Mapping[str, Any]) -> list[tuple[int, str, str]]:
    plan = _case_plan(case)
    markers = (
        (case.get("source_observed_time_ns"), "source observed", "#7652a7"),
        (case.get("interaction_time_ns"), "interaction", "#944b8c"),
        (_case_value(case, "acceptance_retest_time_ns"), "first retest", "#9b6c18"),
        (_case_value(case, "acceptance_response_time_ns"), "completed response", "#2365a7"),
        (plan.get("decision_time_ns"), "plan decision", "#d07a00"),
        (case.get("terminal_time_ns"), "terminal decision", "#111827"),
        (case.get("actual_entry_time_ns"), "actual fill", "#1e3a8a"),
        (case.get("actual_exit_time_ns"), "actual exit", "#111827"),
    )
    output: list[tuple[int, str, str]] = []
    seen: set[tuple[int, str]] = set()
    for value, label, color in markers:
        time_ns = _optional_int(value)
        if time_ns is None or (time_ns, label) in seen:
            continue
        seen.add((time_ns, label))
        output.append((time_ns, label, color))
    return output


def render_case(
    case: Mapping[str, Any],
    raw: pd.DataFrame,
    path: Path,
    *,
    horizon_minutes: int,
) -> dict[str, Any]:
    if raw.empty:
        raise ReviewHarnessError(f"no raw bars for {case.get('symbol')}")
    interaction_ns = _optional_int(case.get("interaction_time_ns"))
    if interaction_ns is None:
        raise ReviewHarnessError(f"episode {case.get('episode_id')} lacks interaction time")
    if case["review_case_kind"] == "ACTUAL_TRADE":
        resolution_ns = _optional_int(case.get("actual_exit_time_ns"))
        if resolution_ns is None:
            raise ReviewHarnessError("actual trade lacks exit time")
        offline_outcome = "NOT_APPLICABLE_ACTUAL_TRADE"
        offline_basis = "NATIVE_ACTUAL_TRADE_LEDGER"
        offline_resolution_ns = resolution_ns
    else:
        terminal_ns = _optional_int(case.get("terminal_time_ns"))
        if terminal_ns is None:
            raise ReviewHarnessError("terminal no-trade lacks terminal time")
        offline_outcome, offline_basis, offline_resolution_ns = evaluate_offline_no_trade(
            case,
            raw,
            horizon_minutes=horizon_minutes,
        )
        resolution_ns = terminal_ns + horizon_minutes * MINUTE_NS
    interaction = _ts(interaction_ns)
    resolution = _ts(resolution_ns)
    context = raw.loc[
        interaction - pd.Timedelta(hours=12): resolution + pd.Timedelta(hours=2)
    ].copy()
    detail = raw.loc[
        interaction - pd.Timedelta(minutes=100): resolution + pd.Timedelta(minutes=40)
    ].copy()
    if context.empty or detail.empty:
        raise ReviewHarnessError(f"chart bars do not cover episode {case.get('episode_id')}")
    context15 = context.resample("15min", label="right", closed="right").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        quote_volume=("quote_volume", "sum"),
        signed_quote_flow=("signed_quote_flow", "sum"),
    ).dropna(subset=["open", "high", "low", "close"])
    level_names = (
        "interaction_source_lower", "interaction_source_upper", "entry", "stop", "target",
        "actual_entry_price", "actual_exit_price", "event_extreme",
    )
    levels = {
        name: _optional_float(_case_value(case, name))
        for name in level_names
    }
    zone = _entry_zone(case)
    extra = [value for value in levels.values() if value is not None]
    if zone is not None:
        extra.extend(zone[:2])
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
        '<rect width="100%" height="100%" fill="#fbfcfe"/>',
    ]
    trade_outcome = str(case.get("actual_outcome") or case.get("trade_outcome") or "")
    title = (
        f"{case['review_case_kind']} | {case.get('symbol')} | {case.get('family')} "
        f"{case.get('side')} | reason={case.get('terminal_reason')} | "
        f"actual={trade_outcome or 'N/A'} | offline={offline_outcome}"
    )
    parts.append(
        f'<text x="{LEFT}" y="31" font-size="18" font-weight="700" fill="{TEXT}">{escape(title)}</text>',
    )
    meta = (
        f"episode={case.get('episode_id')} | source={case.get('source_kind')} "
        f"{case.get('source_timeframe_minutes')}m | grossRR={case.get('gross_rr')} | "
        f"offline basis={offline_basis}"
    )
    parts.append(f'<text x="{LEFT}" y="58" font-size="13" fill="#485564">{escape(meta)}</text>')
    sy_context = _candles(parts, context15, CONTEXT_Y, CONTEXT_H, extra)
    sy_detail = _candles(parts, detail, DETAIL_Y, DETAIL_H, extra)
    for sy in (sy_context, sy_detail):
        if sy is None:
            continue
        source_lower = levels["interaction_source_lower"]
        source_upper = levels["interaction_source_upper"]
        if source_lower is not None and source_upper is not None and source_lower <= source_upper:
            _band(parts, sy, source_lower, source_upper, "direction-owning source", "#7652a7")
        if zone is not None:
            _band(
                parts,
                sy,
                zone[0],
                zone[1],
                f"entry refinement zone: {zone[2]}",
                "#d39522",
            )
        for name, label, color, dash in (
            ("entry", "planned entry", "#2365a7", ""),
            ("stop", "structural stop", "#b82e36", "5 3"),
            ("target", "planned destination", "#25834f", "5 3"),
            ("actual_entry_price", "actual entry", "#1e3a8a", "2 3"),
            ("actual_exit_price", "actual exit", "#111827", "2 3"),
            ("event_extreme", "episode extreme", "#944b8c", "2 4"),
        ):
            value = levels[name]
            if value is not None:
                _hline(parts, sy, value, label, color, dash)
    for index, y, height in (
        (context15.index, CONTEXT_Y, CONTEXT_H),
        (detail.index, DETAIL_Y, DETAIL_H),
    ):
        for time_ns, label, color in _case_markers(case):
            _vline(parts, index, _ts(time_ns), y, height, label, color)
        if offline_resolution_ns is not None and case["review_case_kind"] == "TERMINAL_NO_TRADE":
            _vline(parts, index, _ts(offline_resolution_ns), y, height, "offline resolution", "#6b7280")
    _flow(parts, detail)
    if len(detail):
        for index in np.linspace(0, len(detail) - 1, min(9, len(detail))).astype(int):
            x = LEFT + (index + 0.5) / len(detail) * PLOT_W
            parts.append(
                f'<text x="{x:.1f}" y="{H-18}" font-size="11" text-anchor="middle" '
                f'fill="{TEXT}">{detail.index[index].strftime("%m-%d %H:%M")}</text>',
            )
    parts.append("</svg>")
    path.write_text("\n".join(parts), encoding="utf-8")
    return {
        **dict(case),
        "chart": path.name,
        "offline_future_outcome": offline_outcome,
        "offline_future_outcome_basis": offline_basis,
        "offline_resolution_time_ns": offline_resolution_ns,
        "rendered_first_bar_time_ns": int(context.index[0].value),
        "rendered_last_bar_time_ns": int(context.index[-1].value),
        "rendered_bar_count": int(len(context)),
    }


def _safe_filename(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))
    return text.strip("_.") or "UNKNOWN"


def _write_manifest_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    preferred = [
        "review_case_kind", "chart", "episode_id", "plan_id", "trade_id", "symbol",
        "family", "side", "terminal_reason", "execution_disposition", "actual_outcome",
        "actual_net_r", "offline_future_outcome", "offline_future_outcome_basis",
        "offline_resolution_time_ns", "interaction_time_ns", "terminal_time_ns",
        "actual_entry_time_ns", "actual_exit_time_ns", "entry", "stop", "target",
        "actual_entry_price", "actual_exit_price", "gross_rr", "source_kind",
        "source_timeframe_minutes", "interaction_source_lower", "interaction_source_upper",
        "rendered_first_bar_time_ns", "rendered_last_bar_time_ns", "rendered_bar_count",
    ]
    fields = preferred + sorted({key for row in rows for key in row} - set(preferred))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            serialized = {
                key: json.dumps(value, sort_keys=True, default=str)
                if isinstance(value, (dict, list, tuple)) else value
                for key, value in row.items()
            }
            writer.writerow(serialized)


def _write_index(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    html = [
        '<!doctype html><meta charset="utf-8"><title>Liquidity episode review</title>',
        '<style>body{font:14px sans-serif;background:#f5f7f9}a{display:block;margin:8px;'
        'padding:10px;background:#fff;border:1px solid #ddd;text-decoration:none;color:#17212c}</style>',
        "<h1>Actual trades and terminal no-trade episode reviews</h1>",
    ]
    for row in rows:
        label = (
            f"{row.get('review_case_kind')} {row.get('symbol')} {row.get('family')} "
            f"{row.get('side')} reason={row.get('terminal_reason')} "
            f"actual={row.get('actual_outcome')} offline={row.get('offline_future_outcome')}"
        )
        html.append(f'<a href="{escape(str(row["chart"]))}">{escape(label)}</a>')
    path.write_text("\n".join(html), encoding="utf-8")


def run_review(
    run_dir: str | Path,
    output: str | Path,
    *,
    no_trade_per_group: int = 2,
    all_no_trades: bool = False,
    offline_horizon_minutes: int = 240,
) -> dict[str, Any]:
    if offline_horizon_minutes < 1:
        raise ValueError("offline_horizon_minutes must be positive")
    inputs = load_review_inputs(run_dir)
    trades = pd.read_csv(inputs.trades_path, low_memory=False)
    decisions = pd.read_csv(inputs.decisions_path, low_memory=False)
    _require_columns(trades, ("trade_id", "episode_id", "plan_id"), "trades.csv")
    _require_columns(
        decisions,
        (
            "episode_id", "episode_status", "outcome", "terminal_reason", "symbol",
            "family", "side", "interaction_time_ns", "terminal_time_ns", "plan_id",
            "evidence_json", "plan_json",
        ),
        "episode_decisions.csv",
    )
    trade_rows = trades.to_dict(orient="records")
    decision_rows = decisions.to_dict(orient="records")
    cases = build_review_cases(
        trade_rows,
        decision_rows,
        no_trade_per_group=no_trade_per_group,
        all_no_trades=all_no_trades,
    )
    if not cases:
        raise ReviewHarnessError("no actual trades or terminal no-trades to review")
    destination = Path(output).resolve()
    if destination.exists() and (
        not destination.is_dir() or any(destination.iterdir())
    ):
        raise ReviewHarnessError(f"review output directory is not empty: {destination}")
    raw = load_case_bars(
        inputs.sources,
        cases,
        horizon_minutes=offline_horizon_minutes,
    )
    destination.mkdir(parents=True, exist_ok=True)
    rendered: list[dict[str, Any]] = []
    for ordinal, case in enumerate(cases, start=1):
        symbol = str(case.get("symbol"))
        anchor = _optional_int(case.get("interaction_time_ns")) or 0
        name = (
            f"{ordinal:05d}_{_safe_filename(case['review_case_kind'])}_"
            f"{_safe_filename(symbol)}_{_ts(anchor).strftime('%Y%m%d_%H%M')}_"
            f"{_safe_filename(case.get('episode_id'))}.svg"
        )
        rendered.append(
            render_case(
                case,
                raw.get(symbol, pd.DataFrame()),
                destination / name,
                horizon_minutes=offline_horizon_minutes,
            ),
        )
    _write_manifest_csv(destination / "cases_manifest.csv", rendered)
    _write_index(destination / "index.html", rendered)
    review_manifest = {
        "schema_version": 1,
        "offline_only": True,
        "offline_future_outcome_basis": (
            "OFFLINE_AUDIT_ONLY;STOP_FIRST_ON_SAME_BAR;NOT_POLICY_EVIDENCE"
        ),
        "renderer_provenance": list(PROVENANCE),
        "run_directory": str(inputs.run_dir),
        "run_json_sha256": _sha256_file(inputs.run_path),
        "trades_csv_sha256": _sha256_file(inputs.trades_path),
        "episode_decisions_csv_sha256": _sha256_file(inputs.decisions_path),
        "replay_source_sha": inputs.run.get("source_sha"),
        "replay_source_working_tree_manifest_sha256": inputs.run.get(
            "source_working_tree_manifest_sha256",
        ),
        "data_source_manifest_sha256": inputs.run.get(
            "data_source_manifest_sha256",
        ),
        "run_source_integrity_all_verified": True,
        "trade_archives": [
            {
                "path": record.get("path"),
                "bytes": record.get("bytes"),
                "sha256": record.get("sha256"),
                "checksum_verified": record.get("checksum_verified"),
            }
            for record in inputs.source_records
        ],
        "actual_trade_cases": sum(
            row["review_case_kind"] == "ACTUAL_TRADE" for row in rendered
        ),
        "terminal_no_trade_cases": sum(
            row["review_case_kind"] == "TERMINAL_NO_TRADE" for row in rendered
        ),
        "all_no_trades": all_no_trades,
        "no_trade_per_reason_family_symbol": no_trade_per_group,
        "offline_horizon_minutes": offline_horizon_minutes,
        "outputs": {
            "cases_manifest": "cases_manifest.csv",
            "index": "index.html",
            "svg_count": len(rendered),
        },
    }
    (destination / "review_manifest.json").write_text(
        json.dumps(review_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return review_manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render actual trades and causal terminal no-trade episodes",
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--no-trade-per-group", type=int, default=2)
    parser.add_argument("--all-no-trades", action="store_true")
    parser.add_argument("--offline-horizon-minutes", type=int, default=240)
    args = parser.parse_args(argv)
    try:
        result = run_review(
            args.run_dir,
            args.output,
            no_trade_per_group=args.no_trade_per_group,
            all_no_trades=args.all_no_trades,
            offline_horizon_minutes=args.offline_horizon_minutes,
        )
    except ReviewHarnessError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
