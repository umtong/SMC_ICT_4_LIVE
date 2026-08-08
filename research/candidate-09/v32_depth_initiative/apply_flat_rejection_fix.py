#!/usr/bin/env python3
"""Implementation-only repair: late flat child rejection is not an alpha fault."""
from __future__ import annotations

from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

old_diagnostic = '''                "candidate32_protective_rejection_fail_closes": 0,
'''
new_diagnostic = '''                "candidate32_protective_rejection_fail_closes": 0,
                "candidate32_late_flat_order_rejections": 0,
'''
if old_diagnostic not in text:
    raise RuntimeError("candidate32 diagnostic insertion point not found")
text = text.replace(old_diagnostic, new_diagnostic, 1)

old_method = '''    def on_order_rejected(self, event: Any) -> None:
        super().on_order_rejected(event)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["candidate32_protective_rejection_fail_closes"] = int(
                self.diagnostics["candidate32_protective_rejection_fail_closes"],
            ) + 1
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
'''
new_method = '''    def on_order_rejected(self, event: Any) -> None:
        late_flat_child = (
            self.portfolio.is_flat(self.config.instrument_id)
            and not self.entry_pending
            and self.current_scenario_id is None
        )
        if late_flat_child:
            self.diagnostics["candidate32_late_flat_order_rejections"] = int(
                self.diagnostics["candidate32_late_flat_order_rejections"],
            ) + 1
            return

        super().on_order_rejected(event)
        if not self.portfolio.is_flat(self.config.instrument_id):
            self.diagnostics["candidate32_protective_rejection_fail_closes"] = int(
                self.diagnostics["candidate32_protective_rejection_fail_closes"],
            ) + 1
            self.cancel_all_orders(self.config.instrument_id)
            self.close_all_positions(self.config.instrument_id)
'''
if old_method not in text:
    raise RuntimeError("candidate32 rejection method not found")
text = text.replace(old_method, new_method, 1)
path.write_text(text, encoding="utf-8")
