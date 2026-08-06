#!/usr/bin/env python3
"""Generate v13 from v12 with outcome-independent official tick windows."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "intrinsic_tick_portfolio_v12_nautilus_week.py"
DESTINATION = HERE / "intrinsic_tick_window_v13_nautilus_week.py"


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    start = source.index("def execution_trade_slice(")
    end = source.index("\n\ndef run(", start)
    replacement = '''def execution_trade_windows(
    records: list[Any],
    *,
    plans: list[ScenarioPlan],
    start_ns: int,
    end_ns: int,
    maximum_hold_ns: int,
) -> tuple[list[AggTrade], list[tuple[int, int]]]:
    """Select official ticks from causal plan windows, never from outcomes.

    Every plan contributes [signal-60s, signal+maximum_hold+120s].  This covers
    market initialization, first eligible trade, the entire fixed holding
    contract, time-exit submission and its following fill.  Overlapping windows
    are merged.  Three post-evaluation ticks remain for forced flattening.
    """
    padding_before = 60 * 1_000_000_000
    padding_after = 120 * 1_000_000_000
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
    first_evaluation_added = False
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
        if not first_evaluation_added:
            result.append(trade)
            first_evaluation_added = True
            continue
        while interval_index < len(merged) and ts_ns > merged[interval_index][1]:
            interval_index += 1
        if (
            interval_index < len(merged)
            and merged[interval_index][0] <= ts_ns <= merged[interval_index][1]
        ):
            result.append(trade)
    if not first_evaluation_added:
        raise RuntimeError("no evaluation trade found")
    if flush < FLUSH_TICKS:
        raise RuntimeError(
            f"expected {FLUSH_TICKS} post-evaluation trades, found {flush}",
        )
    return result, merged
'''
    source = source[:start] + replacement + source[end:]
    old_call = '''    execution_trades = execution_trade_slice(
        records,
        start_ns=start_ns,
        end_ns=end_ns,
    )
'''
    new_call = '''    execution_trades, execution_windows = execution_trade_windows(
        records,
        plans=plans,
        start_ns=start_ns,
        end_ns=end_ns,
        maximum_hold_ns=MAXIMUM_HOLD_NS,
    )
'''
    if source.count(old_call) != 1:
        raise RuntimeError("unexpected v12 execution slice call")
    source = source.replace(old_call, new_call, 1)
    old_payload = '        "official_execution_trade_ticks": len(execution_trades),\n'
    new_payload = (
        '        "official_execution_trade_ticks": len(execution_trades),\n'
        '        "tick_selection": (\n'
        '            "outcome-independent union of signal-minus-60s through "\n'
        '            "signal-plus-fixed-hold-plus-120s windows"\n'
        '        ),\n'
        '        "execution_tick_windows": [list(row) for row in execution_windows],\n'
    )
    if source.count(old_payload) != 1:
        raise RuntimeError("unexpected v12 summary payload")
    source = source.replace(old_payload, new_payload, 1)
    source = source.replace(
        "intrinsic_tick_portfolio_v12_summary.json",
        "intrinsic_tick_window_v13_summary.json",
    )
    source = source.replace(
        "candidate-01-v12-tick",
        "candidate-01-v13-tick-window",
    )
    source = source.replace(
        "First-week intrinsic-auction controls on NautilusTrader TradeTicks.",
        "Causal-window intrinsic-auction controls on NautilusTrader TradeTicks.",
        1,
    )
    DESTINATION.write_text(source, encoding="utf-8")
    print(f"wrote {DESTINATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
