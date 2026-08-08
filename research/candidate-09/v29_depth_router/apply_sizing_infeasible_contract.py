#!/usr/bin/env python3
"""Convert minimum-quantity insolvency from an exception into an economic failure.

No signal, size, cost, risk, date, or fill rule changes. Once full-NAV 3% planned-loss
sizing cannot produce the venue minimum quantity, the account is economically unable
to continue; the strategy records this and disables new entries for the remaining bars.
"""
from pathlib import Path

path = Path(__file__).resolve().parent / "nautilus_strategy.py"
text = path.read_text(encoding="utf-8")
if "ACCOUNT_BELOW_MINIMUM_RISK_SIZED_QUANTITY" in text:
    raise SystemExit(0)

old = "        self.missing_feature_bars = 0\n"
new = (
    "        self.missing_feature_bars = 0\n"
    "        self.sizing_infeasible_signals = 0\n"
    "        self._trading_disabled = False\n"
)
if old not in text:
    raise RuntimeError("strategy initialization contract not found")
text = text.replace(old, new, 1)

old = "        self._submit_signal(result.signal)\n\n    def _submit_signal(self, signal: Signal) -> None:\n"
new = (
    "        if self._trading_disabled:\n"
    "            self._record_skipped_signal(\n"
    "                result.signal,\n"
    "                \"ACCOUNT_BELOW_MINIMUM_RISK_SIZED_QUANTITY\",\n"
    "            )\n"
    "            return\n"
    "        self._submit_signal(result.signal)\n\n"
    "    def _submit_signal(self, signal: Signal) -> None:\n"
)
if old not in text:
    raise RuntimeError("strategy submission contract not found")
text = text.replace(old, new, 1)

old = '''        sizing = risk_based_quantity(
            nav=Decimal(str(self.adjusted_nav)),
            risk_fraction=Decimal(str(self.config.risk_fraction)),
            entry_price=Decimal(str(signal.entry_reference)),
            stop_price=Decimal(str(signal.stop_price)),
            cost_rate_per_fill=Decimal(str(self.config.composite_cost_per_fill)),
            quantity_increment=self.instrument.size_increment.as_decimal(),
        )
'''
new = '''        try:
            sizing = risk_based_quantity(
                nav=Decimal(str(self.adjusted_nav)),
                risk_fraction=Decimal(str(self.config.risk_fraction)),
                entry_price=Decimal(str(signal.entry_reference)),
                stop_price=Decimal(str(signal.stop_price)),
                cost_rate_per_fill=Decimal(str(self.config.composite_cost_per_fill)),
                quantity_increment=self.instrument.size_increment.as_decimal(),
            )
        except ValueError as exc:
            if str(exc) != "risk budget below minimum quantity":
                raise
            self.sizing_infeasible_signals += 1
            self._trading_disabled = True
            self.diagnostic_events.append(
                {
                    "scenario_id": signal.scenario_id,
                    "event_type": "ACCOUNT_EXHAUSTED_FOR_MINIMUM_QUANTITY",
                    "event_time_ns": signal.observed_time_ns,
                    "observed_time_ns": signal.observed_time_ns,
                    "previous_state": "ENTERABLE",
                    "next_state": "NO_TRADE",
                    "reason_code": "ACCOUNT_BELOW_MINIMUM_RISK_SIZED_QUANTITY",
                    "reference_price": signal.entry_reference,
                    "details": {
                        "adjusted_nav": self.adjusted_nav,
                        "risk_fraction": float(self.config.risk_fraction),
                    },
                },
            )
            return
'''
if old not in text:
    raise RuntimeError("risk sizing contract not found")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
