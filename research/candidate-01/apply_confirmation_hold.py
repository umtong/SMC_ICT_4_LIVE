#!/usr/bin/env python3
"""Patch the shared probe with an optional delayed-entry confirmation hold."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
PORTFOLIO = HERE / "portfolio_probe.py"
LTF = HERE / "ltf_mss_resting_pool_probe.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


portfolio = PORTFOLIO.read_text(encoding="utf-8")
portfolio = replace_once(
    portfolio,
    '''@dataclass(frozen=True, slots=True)
class Pending:
    symbol: str
    horizon: int
    plan: TradePlan
''',
    '''@dataclass(frozen=True, slots=True)
class Pending:
    symbol: str
    horizon: int
    plan: TradePlan
    confirmation_hold_price: float | None = None
''',
    label="Pending confirmation hold",
)
portfolio = replace_once(
    portfolio,
    '''    price_risk_fraction: float
    net_reward_risk: float
''',
    '''    price_risk_fraction: float
    net_reward_risk: float
    confirmation_hold_price: float | None
''',
    label="Viable confirmation hold",
)
portfolio = replace_once(
    portfolio,
    '''    plan = pending.plan
    entry = bar.close
    if plan.side is Side.LONG and not plan.stop_price < entry < plan.target_price:
''',
    '''    plan = pending.plan
    entry = bar.close
    if pending.confirmation_hold_price is not None:
        hold_ok = (
            entry >= pending.confirmation_hold_price
            if plan.side is Side.LONG
            else entry <= pending.confirmation_hold_price
        )
        if not hold_ok:
            return None
    if plan.side is Side.LONG and not plan.stop_price < entry < plan.target_price:
''',
    label="viable hold gate",
)
portfolio = replace_once(
    portfolio,
    '''        price_risk_fraction=price_fraction,
        net_reward_risk=net_rr,
    )
''',
    '''        price_risk_fraction=price_fraction,
        net_reward_risk=net_rr,
        confirmation_hold_price=pending.confirmation_hold_price,
    )
''',
    label="viable hold persistence",
)
portfolio = replace_once(
    portfolio,
    '''            "net_reward_risk_at_entry": active.viable.net_reward_risk,
            "exit_time_ns": exit_time_ns,
''',
    '''            "net_reward_risk_at_entry": active.viable.net_reward_risk,
            "confirmation_hold_price": active.viable.confirmation_hold_price,
            "exit_time_ns": exit_time_ns,
''',
    label="trade hold evidence",
)
portfolio = replace_once(
    portfolio,
    '''        "insufficient_net_reward_risk": 0,
        "occupied": 0,
    }
''',
    '''        "insufficient_net_reward_risk": 0,
        "failed_confirmation_hold": 0,
        "occupied": 0,
    }
''',
    label="hold rejection counter",
)
portfolio = replace_once(
    portfolio,
    '''                plan = item.plan
                entry = current_bar.close
                geometry_ok = (
''',
    '''                plan = item.plan
                entry = current_bar.close
                if item.confirmation_hold_price is not None:
                    hold_ok = (
                        entry >= item.confirmation_hold_price
                        if plan.side is Side.LONG
                        else entry <= item.confirmation_hold_price
                    )
                    if not hold_ok:
                        rejected["failed_confirmation_hold"] += 1
                        continue
                geometry_ok = (
''',
    label="delayed hold rejection",
)
PORTFOLIO.write_text(portfolio, encoding="utf-8")

ltf = LTF.read_text(encoding="utf-8")
ltf = replace_once(
    ltf,
    '''        sweep_extreme: float,
        reason_code: str,
    ) -> tuple[Pending | None, str, float, float, float]:
''',
    '''        sweep_extreme: float,
        reason_code: str,
        confirmation_hold_price: float | None = None,
    ) -> tuple[Pending | None, str, float, float, float]:
''',
    label="LTF plan hold signature",
)
ltf = replace_once(
    ltf,
    '''        pending = Pending(symbol="BTCUSDT", horizon=60, plan=plan)
''',
    '''        pending = Pending(
            symbol="BTCUSDT",
            horizon=60,
            plan=plan,
            confirmation_hold_price=confirmation_hold_price,
        )
''',
    label="LTF pending hold",
)
ltf = replace_once(
    ltf,
    '''                reason_code="LTF_MSS_DIRECTIONAL_BREAK_CONFIRMED",
            )
''',
    '''                reason_code="LTF_MSS_DIRECTIONAL_BREAK_CONFIRMED",
                confirmation_hold_price=active.internal_break,
            )
''',
    label="directional MSS hold",
)
ltf = replace_once(
    ltf,
    '''                reason_code="LTF_MSS_FLOW_DISPLACEMENT_CONFIRMED",
            )
''',
    '''                reason_code="LTF_MSS_FLOW_DISPLACEMENT_CONFIRMED",
                confirmation_hold_price=active.internal_break,
            )
''',
    label="flow MSS hold",
)
LTF.write_text(ltf, encoding="utf-8")
