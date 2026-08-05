#!/usr/bin/env python3
"""Add an optional scenario-ID gate to the shared fast portfolio probe.

The default is ``None`` and therefore preserves every existing probe result.
Independent scenario detectors can pass a frozen set of causal scenario IDs
without duplicating portfolio accounting, costs, execution delay or the global
one-position constraint.
"""

from pathlib import Path

path = Path(__file__).with_name("portfolio_probe.py")
text = path.read_text(encoding="utf-8")

old_signature = '''    starting_nav: float,
    risk_rates: tuple[float, ...],
) -> tuple[pd.DataFrame, dict[str, Any], dict[float, list[dict[str, Any]]]]:
'''
new_signature = '''    starting_nav: float,
    risk_rates: tuple[float, ...],
    allowed_scenario_ids: frozenset[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[float, list[dict[str, Any]]]]:
'''
old_condition = '''                if plan is not None and start_ns <= ts_ns < end_ns and active is None:
                    generated.append(Pending(symbol=symbol, horizon=horizon, plan=plan))
'''
new_condition = '''                if (
                    plan is not None
                    and start_ns <= ts_ns < end_ns
                    and active is None
                    and (
                        allowed_scenario_ids is None
                        or plan.scenario_id in allowed_scenario_ids
                    )
                ):
                    generated.append(Pending(symbol=symbol, horizon=horizon, plan=plan))
'''

for old, new, label in (
    (old_signature, new_signature, "simulate signature"),
    (old_condition, new_condition, "plan allowlist condition"),
):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one {label} match, found {count}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
