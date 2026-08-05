#!/usr/bin/env python3
"""Patch the event simulator with an optional causal boundary-reacceptance exit."""

from pathlib import Path

path = Path(__file__).resolve().parent / "impact_regime_probe.py"
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '''    starting_nav: float,
    cost: float,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, pd.DataFrame]:
''',
    '''    starting_nav: float,
    cost: float,
    exit_on_boundary_reacceptance: bool = False,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame, pd.DataFrame]:
''',
    "simulate signature",
)
replace_once(
    '''    current_day: str | None = None
    current_day_nav = starting_nav

    for index, feature in enumerate(features):
''',
    '''    current_day: str | None = None
    current_day_nav = starting_nav
    pending_reacceptance_exit = False

    for index, feature in enumerate(features):
''',
    "pending reacceptance state",
)
replace_once(
    '''        occupied_at_start = active is not None
        if active is not None:
''',
    '''        occupied_at_start = active is not None
        if active is not None and pending_reacceptance_exit:
            # The previous completed event re-established outside value. Exit
            # at the next observable event open; live stop/target orders retain
            # precedence when that open gaps through them.
            if active.plan.side is Side.LONG:
                if bar.open <= active.plan.stop_price:
                    reacceptance_price = bar.open
                    reacceptance_reason = "STOP"
                elif bar.open >= active.plan.target_price:
                    reacceptance_price = active.plan.target_price
                    reacceptance_reason = "TARGET"
                else:
                    reacceptance_price = bar.open
                    reacceptance_reason = "BOUNDARY_REACCEPTANCE"
            else:
                if bar.open >= active.plan.stop_price:
                    reacceptance_price = bar.open
                    reacceptance_reason = "STOP"
                elif bar.open <= active.plan.target_price:
                    reacceptance_price = active.plan.target_price
                    reacceptance_reason = "TARGET"
                else:
                    reacceptance_price = bar.open
                    reacceptance_reason = "BOUNDARY_REACCEPTANCE"
            closed = close_position(
                active,
                exit_time_ns=bar.start_time_ns,
                exit_price=reacceptance_price,
                reason=reacceptance_reason,
                cost=cost,
            )
            nav = closed.exit_nav
            trades.append(closed)
            counters["boundary_reacceptance_exits"] += int(
                reacceptance_reason == "BOUNDARY_REACCEPTANCE",
            )
            counters["boundary_reacceptance_stop_gaps"] += int(
                reacceptance_reason == "STOP",
            )
            active = None
            pending_reacceptance_exit = False

        if active is not None:
''',
    "next-open reacceptance exit",
)
replace_once(
    '''        new_plans = schedules.get(index, [])
        if active is None:
''',
    '''        if active is not None and exit_on_boundary_reacceptance:
            reaccepted = (
                bar.close <= active.plan.confirmation_hold_price
                if active.plan.side is Side.LONG
                else bar.close >= active.plan.confirmation_hold_price
            )
            if reaccepted and not pending_reacceptance_exit:
                pending_reacceptance_exit = True
                counters["boundary_reacceptance_signals"] += 1

        new_plans = schedules.get(index, [])
        if active is None:
''',
    "completed-event reacceptance signal",
)
replace_once(
    '''        "target_met": geo >= 0.01,
        "counters": dict(counters),
''',
    '''        "target_met": geo >= 0.01,
        "exit_on_boundary_reacceptance": exit_on_boundary_reacceptance,
        "counters": dict(counters),
''',
    "management evidence",
)
path.write_text(text, encoding="utf-8")
