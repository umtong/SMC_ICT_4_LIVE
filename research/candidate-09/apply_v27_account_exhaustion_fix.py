#!/usr/bin/env python3
"""Convert minimum-quantity account exhaustion into a terminal no-entry state.

This is an execution-lifecycle repair only. It does not alter candidate-09 v27's
market states, signal thresholds, targets, stops, costs, risk fraction, or dates.
The identical broad block must finish and report the economically exhausted NAV
instead of terminating the backtest with an exception.
"""
from pathlib import Path

PATH = Path(__file__).resolve().parent / "v27_session" / "nautilus_strategy.py"


def replace_once(old: str, new: str) -> None:
    text = PATH.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"v27 account-exhaustion patch contract not found in {PATH}")
    PATH.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "        self.missing_feature_bars = 0\n",
    "        self.missing_feature_bars = 0\n"
    "        self._account_exhausted = False\n",
)

replace_once(
    "        result = self.logic.on_bar(flow_bar)\n"
    "        self._record_events(result.events)\n"
    "        if result.signal is None or self._entry_blackout(flow_bar):\n",
    "        result = self.logic.on_bar(flow_bar)\n"
    "        self._record_events(result.events)\n"
    "        if self._account_exhausted:\n"
    "            if result.signal is not None:\n"
    "                self._record_skipped_signal(result.signal, 'ACCOUNT_BELOW_MINIMUM_QUANTITY')\n"
    "            return\n"
    "        if result.signal is None or self._entry_blackout(flow_bar):\n",
)

replace_once(
    "        sizing = risk_based_quantity(\n"
    "            nav=Decimal(str(self.adjusted_nav)),\n"
    "            risk_fraction=Decimal(str(self.config.risk_fraction)),\n"
    "            entry_price=Decimal(str(signal.entry_reference)),\n"
    "            stop_price=Decimal(str(signal.stop_price)),\n"
    "            cost_rate_per_fill=Decimal(str(self.config.composite_cost_per_fill)),\n"
    "            quantity_increment=self.instrument.size_increment.as_decimal(),\n"
    "        )\n",
    "        try:\n"
    "            sizing = risk_based_quantity(\n"
    "                nav=Decimal(str(self.adjusted_nav)),\n"
    "                risk_fraction=Decimal(str(self.config.risk_fraction)),\n"
    "                entry_price=Decimal(str(signal.entry_reference)),\n"
    "                stop_price=Decimal(str(signal.stop_price)),\n"
    "                cost_rate_per_fill=Decimal(str(self.config.composite_cost_per_fill)),\n"
    "                quantity_increment=self.instrument.size_increment.as_decimal(),\n"
    "            )\n"
    "        except ValueError as exc:\n"
    "            if str(exc) != 'risk budget below minimum quantity':\n"
    "                raise\n"
    "            self._account_exhausted = True\n"
    "            self.rejected_orders += 1\n"
    "            self._record_skipped_signal(signal, 'ACCOUNT_BELOW_MINIMUM_QUANTITY')\n"
    "            return\n",
)

print(f"patched {PATH}")
