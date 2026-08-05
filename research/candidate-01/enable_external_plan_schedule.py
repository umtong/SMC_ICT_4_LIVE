#!/usr/bin/env python3
"""Allow independent causal detectors to reuse the shared portfolio simulator.

The optional schedule is keyed by completed signal-bar timestamp and contains
``Pending`` rows.  Defaults preserve all existing behavior.  Callers can pass
an empty scenario allowlist to suppress the built-in failed-auction machines
and supply only their own precomputed causal plans.
"""

from pathlib import Path

path = Path(__file__).with_name("portfolio_probe.py")
text = path.read_text(encoding="utf-8")

old_signature = '''    risk_rates: tuple[float, ...],
    allowed_scenario_ids: frozenset[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[float, list[dict[str, Any]]]]:
'''
new_signature = '''    risk_rates: tuple[float, ...],
    allowed_scenario_ids: frozenset[str] | None = None,
    external_plans_by_signal_time: dict[int, tuple[Pending, ...]] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[float, list[dict[str, Any]]]]:
'''
old_tail = '''                ):
                    generated.append(Pending(symbol=symbol, horizon=horizon, plan=plan))
        pending = generated
'''
new_tail = '''                ):
                    generated.append(Pending(symbol=symbol, horizon=horizon, plan=plan))
        if (
            external_plans_by_signal_time is not None
            and start_ns <= ts_ns < end_ns
            and active is None
        ):
            for item in external_plans_by_signal_time.get(ts_ns, ()):
                if item.symbol in bars_now:
                    generated.append(item)
        pending = generated
'''

for old, new, label in (
    (old_signature, new_signature, "simulate signature"),
    (old_tail, new_tail, "generated-plan tail"),
):
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one {label} match, found {count}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
