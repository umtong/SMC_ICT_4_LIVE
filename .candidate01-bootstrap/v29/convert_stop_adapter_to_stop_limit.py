#!/usr/bin/env python3
"""Convert the generated stop adapter to the supported STOP_LIMIT contract."""
from pathlib import Path

path = Path("research/candidate-01/nautilus_tick_stop_plan_backtest.py")
text = path.read_text(encoding="utf-8")

text = text.replace(
    "Authoritative NautilusTrader TradeTick execution for causal stop entries.",
    "Authoritative NautilusTrader TradeTick execution for causal stop-limit entries.",
    1,
)
text = text.replace(
    "plus a causal stop-entry\ninstruction",
    "plus a causal stop-limit entry\ninstruction",
    1,
)
text = text.replace("owns stop-entry matching", "owns stop-limit entry matching", 1)

old = '''@dataclass(frozen=True, slots=True)
class StopEntryInstruction:
    """Immutable plan plus its causal stop-entry contract."""

    plan: ScenarioPlan
    trigger_price: float
    expiry_time_ns: int
    entry_reason: str

    def __post_init__(self) -> None:
        if self.trigger_price <= 0.0:
            raise ValueError("trigger_price must be positive")
        if self.expiry_time_ns <= int(self.plan.signal_time_ns):
            raise ValueError("expiry_time_ns must be after signal_time_ns")
'''
new = '''@dataclass(frozen=True, slots=True)
class StopEntryInstruction:
    """Immutable plan plus its causal STOP_LIMIT execution contract."""

    plan: ScenarioPlan
    trigger_price: float
    limit_price: float
    expiry_time_ns: int
    entry_reason: str

    def __post_init__(self) -> None:
        if self.trigger_price <= 0.0:
            raise ValueError("trigger_price must be positive")
        if self.limit_price <= 0.0:
            raise ValueError("limit_price must be positive")
        if self.plan.side is Side.LONG and self.limit_price < self.trigger_price:
            raise ValueError("long STOP_LIMIT cap cannot be below its trigger")
        if self.plan.side is Side.SHORT and self.limit_price > self.trigger_price:
            raise ValueError("short STOP_LIMIT floor cannot be above its trigger")
        if self.expiry_time_ns <= int(self.plan.signal_time_ns):
            raise ValueError("expiry_time_ns must be after signal_time_ns")
'''
if old not in text:
    raise SystemExit("stop instruction block not found")
text = text.replace(old, new, 1)

old = '''            self.pending_trigger_price: float | None = None
            self.pending_stop_price: float | None = None
'''
new = '''            self.pending_trigger_price: float | None = None
            self.pending_limit_price: float | None = None
            self.pending_stop_price: float | None = None
'''
if old not in text:
    raise SystemExit("pending state block not found")
text = text.replace(old, new, 1)

old = '''                "planned_trigger_price": instruction.trigger_price,
                "entry_expiry_time_ns": instruction.expiry_time_ns,
'''
new = '''                "planned_trigger_price": instruction.trigger_price,
                "planned_limit_price": instruction.limit_price,
                "entry_expiry_time_ns": instruction.expiry_time_ns,
'''
if old not in text:
    raise SystemExit("rejection evidence block not found")
text = text.replace(old, new, 1)

old = '''            self.pending_trigger_price = None
            self.pending_stop_price = None
'''
new = '''            self.pending_trigger_price = None
            self.pending_limit_price = None
            self.pending_stop_price = None
'''
if old not in text:
    raise SystemExit("clear pending block not found")
text = text.replace(old, new, 1)

start = text.index("        def _geometry(\n")
end = text.index("        def _manage_pending(", start)
geometry = '''        def _geometry(
            self,
            instruction: StopEntryInstruction,
            *,
            ts_ns: int,
        ) -> tuple[float, float, float, float, float, float, float] | None:
            plan = instruction.plan
            trigger = _as_float(
                self.instrument.make_price(instruction.trigger_price),
            )
            # The limit is the worst permitted entry, so risk and reward are
            # evaluated here. Any better actual Nautilus fill risks <= 3% NAV.
            entry = _as_float(self.instrument.make_price(instruction.limit_price))
            stop = _as_float(self.instrument.make_price(plan.stop_price))
            target = _as_float(self.instrument.make_price(plan.target_price))
            rounded_hold = _as_float(
                self.instrument.make_price(plan.confirmation_hold_price),
            )
            hold_ok = (
                trigger >= rounded_hold
                if plan.side is Side.LONG
                else trigger <= rounded_hold
            )
            if not hold_ok:
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="ENTRY_TRIGGER_OUTSIDE_CONFIRMATION_CONTRACT",
                    trigger=trigger,
                    entry=entry,
                    rounded_hold=rounded_hold,
                    stop=stop,
                    target=target,
                )
                return None
            geometry_ok = (
                stop < trigger <= entry < target
                if plan.side is Side.LONG
                else target < entry <= trigger < stop
            )
            if not geometry_ok:
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="INVALID_STOP_LIMIT_ENTRY_GEOMETRY",
                    trigger=trigger,
                    entry=entry,
                    stop=stop,
                    target=target,
                )
                return None

            cost = float(self.config.cost_fraction_per_side)
            price_risk = abs(entry - stop)
            planned_loss = price_risk + entry * cost + stop * cost
            planned_gain = abs(target - entry) - entry * cost - target * cost
            price_fraction = (
                price_risk / planned_loss if planned_loss > 0.0 else 0.0
            )
            net_rr = planned_gain / planned_loss if planned_loss > 0.0 else -1.0
            if price_fraction < float(self.config.minimum_price_risk_fraction):
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="COST_DOMINATED_AT_STOP_LIMIT_ENTRY",
                    trigger=trigger,
                    entry=entry,
                    stop=stop,
                    target=target,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                return None
            if planned_gain <= 0.0 or net_rr < float(
                self.config.minimum_net_reward_risk,
            ):
                self._reject(
                    instruction,
                    ts_ns=ts_ns,
                    reason="INSUFFICIENT_NET_REWARD_RISK_AT_STOP_LIMIT_ENTRY",
                    trigger=trigger,
                    entry=entry,
                    stop=stop,
                    target=target,
                    price_risk_fraction=price_fraction,
                    net_reward_risk=net_rr,
                )
                return None
            return (
                trigger,
                entry,
                stop,
                target,
                planned_loss,
                price_fraction,
                net_rr,
            )

'''
text = text[:start] + geometry + text[end:]

text = text.replace(
    "FAILED_CONFIRMATION_HOLD_BEFORE_LIMIT_ARMING",
    "FAILED_CONFIRMATION_HOLD_BEFORE_STOP_LIMIT_ARMING",
)
text = text.replace(
    '''                    float,
                    StopEntryInstruction,
                    float,
                    float,
                    float,
                    float,
                    float,
                ]''',
    '''                    float,
                    StopEntryInstruction,
                    float,
                    float,
                    float,
                    float,
                    float,
                    float,
                ]''',
    1,
)
old = '''                entry, stop, target, planned_loss, price_fraction, net_rr = geometry
                not_yet_triggered = (
                    entry > current if plan.side is Side.LONG else entry < current
                )
'''
new = '''                (
                    trigger,
                    entry,
                    stop,
                    target,
                    planned_loss,
                    price_fraction,
                    net_rr,
                ) = geometry
                not_yet_triggered = (
                    trigger > current
                    if plan.side is Side.LONG
                    else trigger < current
                )
'''
if old not in text:
    raise SystemExit("geometry unpack block not found")
text = text.replace(old, new, 1)
text = text.replace(
    '''                        observed_price=current,
                        entry=entry,
                    )''',
    '''                        observed_price=current,
                        trigger=trigger,
                        entry=entry,
                    )''',
    1,
)
old = '''                        instruction,
                        entry,
                        stop,
                        target,
                        planned_loss,
                        price_fraction,
'''
new = '''                        instruction,
                        trigger,
                        entry,
                        stop,
                        target,
                        planned_loss,
                        price_fraction,
'''
if old not in text:
    raise SystemExit("viable append block not found")
text = text.replace(old, new, 1)
old = '''                instruction,
                entry,
                stop,
                target,
                planned_loss,
                price_fraction,
            ) = ordered[0]
'''
new = '''                instruction,
                trigger,
                entry,
                stop,
                target,
                planned_loss,
                price_fraction,
            ) = ordered[0]
'''
if old not in text:
    raise SystemExit("viable selected block not found")
text = text.replace(old, new, 1)

old = '''                entry_order_type=OrderType.STOP_MARKET,
                entry_trigger_price=self.instrument.make_price(entry),
                time_in_force=TimeInForce.GTC,
'''
new = '''                entry_order_type=OrderType.STOP_LIMIT,
                entry_trigger_price=self.instrument.make_price(trigger),
                entry_price=self.instrument.make_price(entry),
                time_in_force=TimeInForce.GTC,
'''
if old not in text:
    raise SystemExit("STOP_MARKET bracket block not found")
text = text.replace(old, new, 1)
old = '''            self.pending_trigger_price = entry
            self.pending_stop_price = stop
'''
new = '''            self.pending_trigger_price = trigger
            self.pending_limit_price = entry
            self.pending_stop_price = stop
'''
if old not in text:
    raise SystemExit("pending price assignment block not found")
text = text.replace(old, new, 1)

text = text.replace('"entry_order_type": "STOP_MARKET",', '"entry_order_type": "STOP_LIMIT",')
old = '''                "planned_trigger_price": entry,
                "entry_expiry_time_ns": instruction.expiry_time_ns,
'''
new = '''                "planned_trigger_price": trigger,
                "planned_limit_price": entry,
                "entry_expiry_time_ns": instruction.expiry_time_ns,
'''
if old not in text:
    raise SystemExit("submission prices block not found")
text = text.replace(old, new, 1)
text = text.replace("STOP_BRACKET_SUBMITTED", "STOP_LIMIT_BRACKET_SUBMITTED")
text = text.replace('"entry_order_type": "STOP_MARKET"', '"entry_order_type": "STOP_LIMIT"')
text = text.replace(
    '"stop-market bracket armed on first venue trade strictly after "\n                    "completed signal; entry triggers only on a later "\n                    "NautilusTrader trade at the causal resumption level"',
    '"stop-limit bracket armed on first venue trade strictly after "\n                    "completed signal; a later venue trade triggers the "\n                    "order and the causal 7bp price-protection cap bounds fill"',
)
text = text.replace(
    '"arm on first venue trade strictly after signal; trigger and fill "\n                    "only on later venue trade at the resumption level"',
    '"arm on first venue trade strictly after signal; trigger on a later "\n                    "resumption trade and fill only within the declared cap"',
)
text = text.replace(
    '"pending_invalidation_and_target_first_cancel": True,',
    '"pending_invalidation_and_target_first_cancel": True,\n                "risk_sized_at_worst_permitted_limit_price": True,',
)

required = (
    "limit_price: float",
    "entry_order_type=OrderType.STOP_LIMIT",
    "entry_trigger_price=self.instrument.make_price(trigger)",
    "entry_price=self.instrument.make_price(entry)",
    "risk_sized_at_worst_permitted_limit_price",
    '"entry_order_type": "STOP_LIMIT"',
)
for item in required:
    if item not in text:
        raise SystemExit(f"converted STOP_LIMIT adapter missing {item}")
if "entry_order_type=OrderType.STOP_MARKET" in text:
    raise SystemExit("unsupported STOP_MARKET parent remains")

path.write_text(text, encoding="utf-8")
print(path)
