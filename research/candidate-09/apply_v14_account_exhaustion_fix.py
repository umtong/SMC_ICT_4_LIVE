#!/usr/bin/env python3
"""Classify minimum-quantity infeasibility without hiding implementation errors.

The frozen v14 signal, stop, target, cost, risk fraction, fixed weeks, and long period are
unchanged. The adapter declines only a signal whose risk-based quantity floors below one
exchange increment, records whether that is signal-specific or true cost-floor account
exhaustion, and keeps NautilusTrader running so the long evaluation can finish. Every
unrecognized sizing error, including an invalid configured risk fraction, still raises.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
strategy_path = ROOT / "nautilus_strategy.py"
run_path = ROOT / "run.py"

strategy = strategy_path.read_text(encoding="utf-8")

helper = '''\n\nMINIMUM_QUANTITY_SIZING_ERROR = "risk budget is below one exchange quantity increment"\nNAV_OR_RISK_CONFIGURATION_ERROR = "NAV must be positive and risk_fraction must be in (0, 0.03]"\nACCOUNT_EXHAUSTION_REASONS = {\n    "ACCOUNT_BELOW_COST_ONLY_MINIMUM_QUANTITY",\n    "ACCOUNT_NAV_NON_POSITIVE",\n}\n\n\ndef sizing_failure_reason(\n    message: str,\n    *,\n    nav: Decimal,\n    risk_fraction: Decimal,\n    entry_price: Decimal,\n    cost_rate: Decimal,\n    minimum_quantity: Decimal,\n) -> str | None:\n    """Map only economically explainable sizing failures; never mask bad configuration."""\n\n    valid_risk = Decimal("0") < risk_fraction <= Decimal("0.03")\n    if message == NAV_OR_RISK_CONFIGURATION_ERROR:\n        return "ACCOUNT_NAV_NON_POSITIVE" if nav <= 0 and valid_risk else None\n    if message != MINIMUM_QUANTITY_SIZING_ERROR or not valid_risk:\n        return None\n    if nav <= 0:\n        return "ACCOUNT_NAV_NON_POSITIVE"\n    # This is a lower bound: even a zero-distance stop must pay both modeled fills.\n    cost_only_minimum_loss = entry_price * cost_rate * Decimal("2") * minimum_quantity\n    if nav * risk_fraction < cost_only_minimum_loss:\n        return "ACCOUNT_BELOW_COST_ONLY_MINIMUM_QUANTITY"\n    return "RISK_BUDGET_BELOW_SIGNAL_MINIMUM_QUANTITY"\n'''
anchor = "\n\nclass Candidate09StrategyConfig(StrategyConfig, frozen=True):"
if "def sizing_failure_reason(" not in strategy:
    if anchor not in strategy:
        raise SystemExit("strategy config anchor not found")
    strategy = strategy.replace(anchor, helper + anchor, 1)

if "self.sizing_infeasible_signals = 0" not in strategy:
    anchor = "        self.missing_feature_bars = 0\n"
    if anchor not in strategy:
        raise SystemExit("strategy counter anchor not found")
    strategy = strategy.replace(
        anchor,
        anchor
        + "        self.sizing_infeasible_signals = 0\n"
        + "        self.account_exhaustion_signals = 0\n",
        1,
    )

old_sizing = '''        sizing = risk_based_quantity(\n            nav=Decimal(str(self.adjusted_nav)),\n            risk_fraction=Decimal(str(self.config.risk_fraction)),\n            entry_price=Decimal(str(signal.entry_reference)),\n            stop_price=Decimal(str(signal.stop_price)),\n            cost_rate_per_fill=Decimal(str(self.config.composite_cost_per_fill)),\n            quantity_increment=self.instrument.size_increment.as_decimal(),\n        )\n'''
new_sizing = '''        nav = Decimal(str(self.adjusted_nav))\n        risk_fraction = Decimal(str(self.config.risk_fraction))\n        entry_price = Decimal(str(signal.entry_reference))\n        stop_price = Decimal(str(signal.stop_price))\n        cost_rate = Decimal(str(self.config.composite_cost_per_fill))\n        quantity_increment = self.instrument.size_increment.as_decimal()\n        try:\n            sizing = risk_based_quantity(\n                nav=nav,\n                risk_fraction=risk_fraction,\n                entry_price=entry_price,\n                stop_price=stop_price,\n                cost_rate_per_fill=cost_rate,\n                quantity_increment=quantity_increment,\n            )\n        except ValueError as exc:\n            reason = sizing_failure_reason(\n                str(exc),\n                nav=nav,\n                risk_fraction=risk_fraction,\n                entry_price=entry_price,\n                cost_rate=cost_rate,\n                minimum_quantity=quantity_increment,\n            )\n            if reason is None:\n                raise\n            per_unit_expected_loss = (\n                abs(entry_price - stop_price)\n                + entry_price * cost_rate\n                + stop_price * cost_rate\n            )\n            self._record_sizing_infeasible(\n                signal,\n                reason=reason,\n                message=str(exc),\n                nav=nav,\n                loss_budget=max(nav, Decimal("0")) * risk_fraction,\n                per_unit_expected_loss=per_unit_expected_loss,\n                minimum_quantity=quantity_increment,\n                account_exhausted=reason in ACCOUNT_EXHAUSTION_REASONS,\n            )\n            return\n'''
if new_sizing not in strategy:
    if old_sizing not in strategy:
        raise SystemExit("direct sizing block not found")
    strategy = strategy.replace(old_sizing, new_sizing, 1)

method = '''\n    def _record_sizing_infeasible(\n        self,\n        signal: Signal,\n        *,\n        reason: str,\n        message: str,\n        nav: Decimal,\n        loss_budget: Decimal,\n        per_unit_expected_loss: Decimal,\n        minimum_quantity: Decimal,\n        account_exhausted: bool,\n    ) -> None:\n        self.sizing_infeasible_signals += 1\n        if account_exhausted:\n            self.account_exhaustion_signals += 1\n        self.diagnostic_events.append(\n            {\n                "scenario_id": signal.scenario_id,\n                "event_type": "ENTRY_SKIPPED",\n                "event_time_ns": signal.observed_time_ns,\n                "observed_time_ns": signal.observed_time_ns,\n                "previous_state": "ENTERABLE",\n                "next_state": "ACCOUNT_EXHAUSTED" if account_exhausted else "NO_TRADE",\n                "reason_code": reason,\n                "reference_price": signal.entry_reference,\n                "details": {\n                    "branch": signal.branch,\n                    "side": signal.side,\n                    "sizing_error": message,\n                    "account_exhausted": account_exhausted,\n                    "adjusted_nav": float(nav),\n                    "loss_budget": float(loss_budget),\n                    "per_unit_expected_loss": float(per_unit_expected_loss),\n                    "minimum_quantity": float(minimum_quantity),\n                    "minimum_quantity_planned_loss": float(\n                        per_unit_expected_loss * minimum_quantity\n                    ),\n                    "cost_only_minimum_quantity_loss": float(\n                        Decimal(str(signal.entry_reference))\n                        * Decimal(str(self.config.composite_cost_per_fill))\n                        * Decimal("2")\n                        * minimum_quantity\n                    ),\n                },\n            },\n        )\n\n'''
anchor = "    def on_order_filled(self, event: OrderFilled) -> None:\n"
if "def _record_sizing_infeasible(" not in strategy:
    if anchor not in strategy:
        raise SystemExit("order-filled anchor not found")
    strategy = strategy.replace(anchor, method + anchor, 1)

required_strategy = (
    "def sizing_failure_reason(",
    "self.sizing_infeasible_signals = 0",
    "self.account_exhaustion_signals = 0",
    "except ValueError as exc:",
    "if reason is None:\n                raise",
    "def _record_sizing_infeasible(",
    '"next_state": "ACCOUNT_EXHAUSTED" if account_exhausted else "NO_TRADE"',
)
missing = [item for item in required_strategy if item not in strategy]
if missing:
    raise SystemExit(f"incomplete strategy account-exhaustion fix: {missing}")
strategy_path.write_text(strategy, encoding="utf-8")

run = run_path.read_text(encoding="utf-8")

constant = '''\nSIZING_INFEASIBLE_REASONS = {\n    "RISK_BUDGET_BELOW_SIGNAL_MINIMUM_QUANTITY",\n    "ACCOUNT_BELOW_COST_ONLY_MINIMUM_QUANTITY",\n    "ACCOUNT_NAV_NON_POSITIVE",\n}\nACCOUNT_EXHAUSTION_REASONS = {\n    "ACCOUNT_BELOW_COST_ONLY_MINIMUM_QUANTITY",\n    "ACCOUNT_NAV_NON_POSITIVE",\n}\n'''
anchor = "DAY_NS = 86_400_000_000_000\n"
if "SIZING_INFEASIBLE_REASONS" not in run:
    if anchor not in run:
        raise SystemExit("day constant anchor not found")
    run = run.replace(anchor, anchor + constant, 1)

helper = '''\n\ndef event_reason_count(details: Sequence[DetailedRun], reasons: set[str]) -> int:\n    return sum(\n        event.get("reason_code") in reasons\n        for detail in details\n        for event in detail.events\n    )\n\n\ndef sizing_infeasible_signal_count(details: Sequence[DetailedRun]) -> int:\n    return event_reason_count(details, SIZING_INFEASIBLE_REASONS)\n\n\ndef account_exhaustion_signal_count(details: Sequence[DetailedRun]) -> int:\n    return event_reason_count(details, ACCOUNT_EXHAUSTION_REASONS)\n'''
anchor = "\ndef evaluate_gate("
if "def account_exhaustion_signal_count(" not in run:
    if anchor not in run:
        raise SystemExit("evaluate-gate anchor not found")
    run = run.replace(anchor, helper + anchor, 1)

if "sizing_infeasible = sizing_infeasible_signal_count(baseline)" not in run:
    anchor = '    gate = config["gate"]\n'
    if anchor not in run:
        raise SystemExit("gate config anchor not found")
    run = run.replace(
        anchor,
        anchor
        + "    sizing_infeasible = sizing_infeasible_signal_count(baseline)\n"
        + "    account_exhaustion = account_exhaustion_signal_count(baseline)\n",
        1,
    )

if '"account_remained_recoverable": account_exhaustion == 0,' not in run:
    anchor = '        "implementation_ok": pooled["implementation_ok"],\n'
    if anchor not in run:
        raise SystemExit("gate implementation check anchor not found")
    run = run.replace(
        anchor,
        anchor + '        "account_remained_recoverable": account_exhaustion == 0,\n',
        1,
    )

old = '    return all(checks.values()), {"pooled": pooled, "checks": checks}\n'
new = '''    return all(checks.values()), {\n        "pooled": pooled,\n        "checks": checks,\n        "sizing_infeasible_signals": sizing_infeasible,\n        "account_exhaustion_signals": account_exhaustion,\n    }\n'''
if new not in run:
    if old not in run:
        raise SystemExit("gate return anchor not found")
    run = run.replace(old, new, 1)

if "sizing_infeasible = sizing_infeasible_signal_count([detail])" not in run:
    anchor = '    minimum_trades = math.ceil(outcome.calendar_days * float(spec["minimum_trades_per_calendar_day"]))\n'
    if anchor not in run:
        raise SystemExit("long minimum-trades anchor not found")
    run = run.replace(
        anchor,
        anchor
        + "    sizing_infeasible = sizing_infeasible_signal_count([detail])\n"
        + "    account_exhaustion = account_exhaustion_signal_count([detail])\n",
        1,
    )

if run.count('"account_remained_recoverable": account_exhaustion == 0,') < 2:
    anchor = '        "implementation_ok": outcome.implementation_status == "OK",\n'
    if anchor not in run:
        raise SystemExit("long implementation check anchor not found")
    run = run.replace(
        anchor,
        anchor + '        "account_remained_recoverable": account_exhaustion == 0,\n',
        1,
    )

anchor = '        "active_months": months,\n'
addition = (
    '        "sizing_infeasible_signals": sizing_infeasible,\n'
    '        "account_exhaustion_signals": account_exhaustion,\n'
)
if '        "account_exhaustion_signals": account_exhaustion,\n' not in run:
    if anchor not in run:
        raise SystemExit("long result anchor not found")
    run = run.replace(anchor, anchor + addition, 1)

required_run = (
    "SIZING_INFEASIBLE_REASONS",
    "ACCOUNT_EXHAUSTION_REASONS",
    "def sizing_infeasible_signal_count(",
    "def account_exhaustion_signal_count(",
    "sizing_infeasible = sizing_infeasible_signal_count(baseline)",
    "sizing_infeasible = sizing_infeasible_signal_count([detail])",
    '"sizing_infeasible_signals": sizing_infeasible',
    '"account_exhaustion_signals": account_exhaustion',
)
missing = [item for item in required_run if item not in run]
if missing:
    raise SystemExit(f"incomplete run account-exhaustion fix: {missing}")
if run.count('"account_remained_recoverable": account_exhaustion == 0,') != 2:
    raise SystemExit("expected exact gate and long recoverability checks")
run_path.write_text(run, encoding="utf-8")
