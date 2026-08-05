#!/usr/bin/env python3
"""Add an optional causal displacement-flow gate to the reusable probe."""

from pathlib import Path

path = Path(__file__).with_name("portfolio_probe.py")
text = path.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one probe patch match, found {count}: {old[:100]!r}")
    text = text.replace(old, new, 1)


replace_once(
    '''    starting_nav: float,
    risk_rates: tuple[float, ...],
) -> tuple[pd.DataFrame, dict[str, Any], dict[float, list[dict[str, Any]]]]:
''',
    '''    starting_nav: float,
    risk_rates: tuple[float, ...],
    minimum_trade_direction_flow_z: float | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[float, list[dict[str, Any]]]]:
''',
)
replace_once(
    '''        "insufficient_net_reward_risk": 0,
        "occupied": 0,
    }
''',
    '''        "insufficient_net_reward_risk": 0,
        "weak_reversal_flow": 0,
        "occupied": 0,
    }
''',
)
replace_once(
    '''            for horizon in variant.horizons:
                plan = machines[(symbol, horizon)].on_bar(current_bar)
                if plan is not None and start_ns <= ts_ns < end_ns and active is None:
                    generated.append(Pending(symbol=symbol, horizon=horizon, plan=plan))
        pending = generated
''',
    '''            for horizon in variant.horizons:
                machine = machines[(symbol, horizon)]
                plan = machine.on_bar(current_bar)
                if plan is not None and start_ns <= ts_ns < end_ns and active is None:
                    aligned_flow_z = None
                    for transition in reversed(machine.transitions):
                        if (
                            transition.scenario_id == plan.scenario_id
                            and transition.event_type == "REVERSAL_DISPLACEMENT_CONFIRMED"
                        ):
                            aligned_flow_z = float(transition.details["flow_z"]) * plan.side.sign
                            break
                    if aligned_flow_z is None:
                        raise RuntimeError(
                            f"trade plan has no causal displacement transition: {plan.scenario_id}",
                        )
                    if (
                        minimum_trade_direction_flow_z is not None
                        and aligned_flow_z < minimum_trade_direction_flow_z
                    ):
                        rejected["weak_reversal_flow"] += 1
                    else:
                        generated.append(Pending(symbol=symbol, horizon=horizon, plan=plan))
        pending = generated
''',
)
replace_once(
    '''        "rejections": rejected,
        "risk_metrics": risk_metrics,
    }
''',
    '''        "rejections": rejected,
        "minimum_trade_direction_flow_z": minimum_trade_direction_flow_z,
        "risk_metrics": risk_metrics,
    }
''',
)
path.write_text(text, encoding="utf-8")
