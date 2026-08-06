#!/usr/bin/env python3
"""Patch integer-safe UTC dates and generate v15 daily-marker windows."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE / "nautilus_plan_backtest.py"
SOURCE = HERE / "intrinsic_tick_window_v13_nautilus_week.py"
DESTINATION = HERE / "intrinsic_tick_daily_markers_v15_nautilus_week.py"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


def patch_utc_date() -> None:
    source = SHARED.read_text(encoding="utf-8")
    old = '''def _utc_date(ts_ns: int) -> str:
    return datetime.fromtimestamp(
        ts_ns / 1_000_000_000,
        tz=timezone.utc,
    ).date().isoformat()
'''
    new = '''def _utc_date(ts_ns: int) -> str:
    # Never round a nanosecond timestamp through binary floating point.  Values
    # such as evaluation_end_ns - 1 can otherwise round to the next UTC day.
    return pd.Timestamp(int(ts_ns), unit="ns", tz="UTC").date().isoformat()
'''
    source = replace_once(source, old, new, "integer-safe UTC date")
    SHARED.write_text(source, encoding="utf-8")


def generate_v15() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("def execution_trade_windows(")
    end = source.index("\n\ndef run(", start)
    replacement = '''def execution_trade_windows(
    records: list[Any],
    *,
    plans: list[ScenarioPlan],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    """Select outcome-independent plan windows plus one UTC-day marker tick.

    Each plan contributes [signal-60s, signal+fixed-hold+120s].  The first
    official trade of every evaluation UTC day is also retained solely to mark
    Nautilus account equity on flat days.  It cannot alter strategy decisions
    because plans remain eligible only strictly after their own signal times.
    """
    padding_before = 60 * 1_000_000_000
    padding_after = 120 * 1_000_000_000
    ns_per_day = 86_400 * 1_000_000_000
    intervals = sorted(
        (
            max(start_ns, int(plan.signal_time_ns) - padding_before),
            min(
                end_ns - 1,
                int(plan.signal_time_ns) + maximum_hold_ns + padding_after,
            ),
        )
        for plan in plans
        if start_ns <= int(plan.signal_time_ns) < end_ns
    )
    merged: list[tuple[int, int]] = []
    for left, right in intervals:
        if right < left:
            continue
        if not merged or left > merged[-1][1] + 1:
            merged.append((left, right))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))

    result: list[AggTrade] = []
    interval_index = 0
    flush = 0
    last_evaluation_day: int | None = None
    evaluation_days_seen: set[int] = set()
    for trade in iter_downloads(records):
        ts_ns = int(trade.ts_event_ns)
        if ts_ns < start_ns:
            continue
        if ts_ns >= end_ns:
            if flush < FLUSH_TICKS:
                result.append(trade)
                flush += 1
                continue
            break

        day_id = ts_ns // ns_per_day
        is_day_marker = day_id != last_evaluation_day
        if is_day_marker:
            last_evaluation_day = day_id
            evaluation_days_seen.add(day_id)
            result.append(trade)
            continue

        while interval_index < len(merged) and ts_ns > merged[interval_index][1]:
            interval_index += 1
        if (
            interval_index < len(merged)
            and merged[interval_index][0] <= ts_ns <= merged[interval_index][1]
        ):
            result.append(trade)
    expected_days = (end_ns - start_ns) // ns_per_day
    if len(evaluation_days_seen) != expected_days:
        raise RuntimeError(
            f"expected {expected_days} evaluation day markers, "
            f"found {len(evaluation_days_seen)}"
        )
    if flush < FLUSH_TICKS:
        raise RuntimeError(
            f"expected {FLUSH_TICKS} post-evaluation trades, found {flush}",
        )
    return result, merged
'''
    source = source[:start] + replacement + source[end:]
    source = source.replace(
        "intrinsic_tick_window_v13_summary.json",
        "intrinsic_tick_daily_markers_v15_summary.json",
    )
    source = source.replace(
        "candidate-01-v13-tick-window",
        "candidate-01-v15-daily-markers",
    )
    source = source.replace(
        '            "outcome-independent union of signal-minus-60s through "\n'
        '            "signal-plus-fixed-hold-plus-120s windows"',
        '            "outcome-independent plan windows plus the first official "\n'
        '            "trade of each evaluation UTC day for NAV marking"',
    )
    source = source.replace(
        "Causal-window intrinsic-auction controls on NautilusTrader TradeTicks.",
        "Daily-marked causal-window controls on NautilusTrader TradeTicks.",
        1,
    )
    DESTINATION.write_text(source, encoding="utf-8")


def main() -> int:
    patch_utc_date()
    generate_v15()
    print("patched integer-safe UTC dates and wrote v15 daily-marker runner")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
