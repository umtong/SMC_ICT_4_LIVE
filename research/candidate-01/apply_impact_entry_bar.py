#!/usr/bin/env python3
"""Patch impact-regime execution to evaluate the entry bar stop-first."""

from pathlib import Path

path = Path(__file__).resolve().parent / "impact_regime_probe.py"
text = path.read_text(encoding="utf-8")
old = '''                counters["entries"] += 1
                counters["occupied_competing_plans"] += max(len(viable) - 1, 0)
        elif pending:
'''
new = '''                counters["entries"] += 1
                counters["occupied_competing_plans"] += max(len(viable) - 1, 0)
                # Entry occurs at this event bar's first trade. Its completed
                # high/low must therefore participate in execution. Resolve a
                # bar touching both stop and target conservatively stop-first.
                adverse = bar.low if active.plan.side is Side.LONG else bar.high
                favorable = bar.high if active.plan.side is Side.LONG else bar.low
                active.minimum_mark_r = min(active.minimum_mark_r, mark_r(active, adverse, cost))
                active.maximum_mark_r = max(active.maximum_mark_r, mark_r(active, favorable, cost))
                if active.plan.side is Side.LONG:
                    entry_stop_hit = bar.low <= active.plan.stop_price
                    entry_target_hit = bar.high >= active.plan.target_price
                else:
                    entry_stop_hit = bar.high >= active.plan.stop_price
                    entry_target_hit = bar.low <= active.plan.target_price
                if entry_stop_hit or entry_target_hit:
                    if entry_stop_hit and entry_target_hit:
                        counters["entry_bar_stop_first"] += 1
                    entry_exit_price = (
                        active.plan.stop_price
                        if entry_stop_hit
                        else active.plan.target_price
                    )
                    entry_exit_reason = "STOP" if entry_stop_hit else "TARGET"
                    closed = close_position(
                        active,
                        exit_time_ns=bar.end_time_ns,
                        exit_price=entry_exit_price,
                        reason=entry_exit_reason,
                        cost=cost,
                    )
                    nav = closed.exit_nav
                    trades.append(closed)
                    active = None
        elif pending:
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one entry insertion point, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
