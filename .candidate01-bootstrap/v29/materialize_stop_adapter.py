#!/usr/bin/env python3
"""Generate the authoritative STOP_MARKET TradeTick adapter from the audited limit adapter."""
from pathlib import Path

source = Path("research/candidate-01/nautilus_tick_limit_plan_backtest.py")
destination = Path("research/candidate-01/nautilus_tick_stop_plan_backtest.py")
text = source.read_text(encoding="utf-8")

replacements = (
    (
        "Authoritative NautilusTrader TradeTick execution for causal resting entries.",
        "Authoritative NautilusTrader TradeTick execution for causal stop entries.",
    ),
    (
        "completed ScenarioPlan objects plus a causal resting\nentry instruction",
        "completed ScenarioPlan objects plus a causal stop-entry\ninstruction",
    ),
    ("limit-entry matching", "stop-entry matching"),
    ("RestingEntryInstruction", "StopEntryInstruction"),
    ("RestingPlanStrategyConfig", "StopPlanStrategyConfig"),
    ("RestingPlanStrategy", "StopPlanStrategy"),
    ("run_nautilus_tick_limit_plan_backtest", "run_nautilus_tick_stop_plan_backtest"),
    ("resting-entry brackets", "stop-entry brackets"),
    ("resting-entry contract", "stop-entry contract"),
    ("entry_price: float", "trigger_price: float"),
    ("self.entry_price", "self.trigger_price"),
    ("entry_price must be positive", "trigger_price must be positive"),
    ("instruction.entry_price", "instruction.trigger_price"),
    ("pending_entry_price", "pending_trigger_price"),
    ("planned_entry_price", "planned_trigger_price"),
    ("limit_entries_expired", "stop_entries_expired"),
    ("LIMIT_ENTRY", "STOP_ENTRY"),
    ("PENDING_LIMIT", "PENDING_STOP"),
    ("LOWER_NET_RR_COMPETING_LIMIT_PLAN", "LOWER_NET_RR_COMPETING_STOP_PLAN"),
    ("COST_DOMINATED_AT_LIMIT_ENTRY", "COST_DOMINATED_AT_STOP_ENTRY"),
    (
        "INSUFFICIENT_NET_REWARD_RISK_AT_LIMIT_ENTRY",
        "INSUFFICIENT_NET_REWARD_RISK_AT_STOP_ENTRY",
    ),
    ("INVALID_LIMIT_ENTRY_GEOMETRY", "INVALID_STOP_ENTRY_GEOMETRY"),
    (
        "ENTRY_LIMIT_OUTSIDE_CONFIRMATION_CONTRACT",
        "ENTRY_TRIGGER_OUTSIDE_CONFIRMATION_CONTRACT",
    ),
    ("LIMIT_ENTRY_RESPONSE_WINDOW_EXPIRED", "STOP_ENTRY_RESPONSE_WINDOW_EXPIRED"),
    ("LIMIT_ENTRY_ALREADY_EXPIRED_AT_ARMING", "STOP_ENTRY_ALREADY_EXPIRED_AT_ARMING"),
    ("LIMIT_BRACKET_SUBMITTED", "STOP_BRACKET_SUBMITTED"),
    ("limit bracket", "stop-market bracket"),
    ("limit entry", "stop entry"),
)
for old, new in replacements:
    text = text.replace(old, new)

old = '''            self.pending_target_price: float | None = None
            self.position_opened_ns: int | None = None
'''
new = '''            self.pending_target_price: float | None = None
            self.position_opened_ns: int | None = None
'''
if old not in text:
    raise SystemExit("pending state anchor missing")
text = text.replace(old, new, 1)

old = '''            self.stop_entries_expired = 0
            self.targets_consumed_before_entry = 0
            self.pending_entries_canceled_at_end = 0
'''
new = '''            self.stop_entries_expired = 0
            self.targets_consumed_before_entry = 0
            self.invalidations_before_entry = 0
            self.pending_entries_canceled_at_end = 0
'''
if old not in text:
    raise SystemExit("counter block missing")
text = text.replace(old, new, 1)

old = '''            plan = instruction.plan
            target = float(self.pending_target_price or plan.target_price)
            target_consumed = (
                price >= target if plan.side is Side.LONG else price <= target
            )
            if target_consumed:
                self.targets_consumed_before_entry += 1
                self._cancel_pending(
                    ts_ns=ts_ns,
                    reason="TARGET_CONSUMED_BEFORE_STOP_ENTRY",
                    observed_price=price,
                )
                return True
            if ts_ns >= int(instruction.expiry_time_ns):
'''
new = '''            plan = instruction.plan
            target = float(self.pending_target_price or plan.target_price)
            stop = float(self.pending_stop_price or plan.stop_price)
            invalidated = (
                price <= stop if plan.side is Side.LONG else price >= stop
            )
            if invalidated:
                self.invalidations_before_entry += 1
                self._cancel_pending(
                    ts_ns=ts_ns,
                    reason="INVALIDATION_CONSUMED_BEFORE_STOP_ENTRY",
                    observed_price=price,
                )
                return True
            target_consumed = (
                price >= target if plan.side is Side.LONG else price <= target
            )
            if target_consumed:
                self.targets_consumed_before_entry += 1
                self._cancel_pending(
                    ts_ns=ts_ns,
                    reason="TARGET_CONSUMED_BEFORE_STOP_ENTRY",
                    observed_price=price,
                )
                return True
            if ts_ns >= int(instruction.expiry_time_ns):
'''
if old not in text:
    raise SystemExit("pending management block missing")
text = text.replace(old, new, 1)

old = '''                passive_or_touch = (
                    entry <= current if plan.side is Side.LONG else entry >= current
                )
                if not passive_or_touch:
                    self._reject(
                        instruction,
                        ts_ns=ts_ns,
                        reason="STOP_ENTRY_WOULD_CHASE_AFTER_HOLD_FAILURE",
                        observed_price=current,
                        entry=entry,
                    )
                    continue
'''
new = '''                not_yet_triggered = (
                    entry > current if plan.side is Side.LONG else entry < current
                )
                if not not_yet_triggered:
                    self._reject(
                        instruction,
                        ts_ns=ts_ns,
                        reason="STOP_ENTRY_TRIGGER_ALREADY_CROSSED_AT_ARMING",
                        observed_price=current,
                        entry=entry,
                    )
                    continue
'''
if old not in text:
    raise SystemExit("arming-side block missing")
text = text.replace(old, new, 1)

old = '''                entry_order_type=OrderType.LIMIT,
                entry_price=self.instrument.make_price(entry),
'''
new = '''                entry_order_type=OrderType.STOP_MARKET,
                entry_trigger_price=self.instrument.make_price(entry),
'''
if old not in text:
    raise SystemExit("entry order block missing")
text = text.replace(old, new, 1)

text = text.replace('"entry_order_type": "LIMIT",', '"entry_order_type": "STOP_MARKET",')
text = text.replace('"entry_order_type": "LIMIT"', '"entry_order_type": "STOP_MARKET"')
text = text.replace(
    '"stop-market bracket armed on first venue trade strictly after "\n                    "completed signal; entry rests at causal confirmation "\n                    "boundary and is matched only by later NautilusTrader "\n                    "trade-tick processing"',
    '"stop-market bracket armed on first venue trade strictly after "\n                    "completed signal; entry triggers only on a later "\n                    "NautilusTrader trade at the causal resumption level"',
)
text = text.replace(
    '"arm on first venue trade strictly after signal; fill only "\n                    "on later venue trade at confirmation boundary or better"',
    '"arm on first venue trade strictly after signal; trigger and fill "\n                    "only on later venue trade at the resumption level"',
)

old = '''                "targets_consumed_before_entry": (
                    strategy.targets_consumed_before_entry
                ),
                "pending_entries_canceled_at_end": (
'''
new = '''                "targets_consumed_before_entry": (
                    strategy.targets_consumed_before_entry
                ),
                "invalidations_before_entry": strategy.invalidations_before_entry,
                "pending_entries_canceled_at_end": (
'''
if old not in text:
    raise SystemExit("metrics counter block missing")
text = text.replace(old, new, 1)

text = text.replace(
    '"one_global_pending_or_open_position": True,',
    '"one_global_pending_or_open_position": True,\n                "pending_invalidation_and_target_first_cancel": True,',
)

required = (
    "class StopEntryInstruction",
    "def run_nautilus_tick_stop_plan_backtest",
    "entry_order_type=OrderType.STOP_MARKET",
    "entry_trigger_price=self.instrument.make_price(entry)",
    "INVALIDATION_CONSUMED_BEFORE_STOP_ENTRY",
    "STOP_ENTRY_TRIGGER_ALREADY_CROSSED_AT_ARMING",
    '"entry_order_type": "STOP_MARKET"',
)
for item in required:
    if item not in text:
        raise SystemExit(f"generated stop adapter missing {item}")

destination.write_text(text, encoding="utf-8")
print(destination)
