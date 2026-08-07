#!/usr/bin/env python3
"""Apply idempotent cross-market causal and execution-boundary repairs."""
from __future__ import annotations

from pathlib import Path

SAME_BATCH_MARKER = "C11_CROSS_SAME_BATCH_CONFIRMATION"
EVENT_FLOOR_MARKER = "C11_CROSS_COMPLETION_EVENT"
PARTIAL_MARKER = "C11_CROSS_PARTIAL_FAIL_CLOSED"
RISK_TYPE_MARKER = "C11_CROSS_RISK_FLOAT_BOUNDARY"


def patch_detector(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    if SAME_BATCH_MARKER in source:
        return 0
    old = '''        self._detect_shock(ts_ns)
        return []
'''
    new = '''        self._detect_shock(ts_ns)
        # C11_CROSS_SAME_BATCH_CONFIRMATION: all values are from the same fully
        # completed synchronized minute. A follower which confirms during the
        # leader's detection batch may therefore be evaluated immediately.
        if self.active is not None:
            return self._evaluate_followers(ts_ns)
        return []
'''
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"same-batch detector anchor count={count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def patch_runner(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    changed = 0
    if RISK_TYPE_MARKER not in source:
        old = '            self.sizer = RiskSizer(Decimal(str(account["risk_fraction"])))\n'
        new = '''            # C11_CROSS_RISK_FLOAT_BOUNDARY: RiskSizer owns Decimal
            # conversion internally. Passing a Decimal into its float-bounded
            # constructor can make exact 0.03 compare above binary 0.03.
            self.sizer = RiskSizer(float(account["risk_fraction"]))
'''
        count = source.count(old)
        if count != 1:
            raise SystemExit(f"risk-sizer runner anchor count={count}")
        source = source.replace(old, new, 1)
        changed += 1
    if PARTIAL_MARKER not in source:
        old = '''            if self.position_open and self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                if not self.portfolio.is_flat(instrument_id):
                    self.lifecycle.append({
                        "type": "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED",
'''
        new = '''            # C11_CROSS_PARTIAL_FAIL_CLOSED: inspect the Nautilus
            # portfolio directly. The parent-expiry event can race the strategy's
            # internal entry-state transition after a partial fill.
            if self.active_symbol is not None:
                instrument_id = instruments[self.active_symbol].id
                if not self.portfolio.is_flat(instrument_id):
                    self.lifecycle.append({
                        "type": "PARTIAL_ENTRY_EXPIRED_FAIL_CLOSED",
'''
        count = source.count(old)
        if count != 1:
            raise SystemExit(f"partial-entry runner anchor count={count}")
        source = source.replace(old, new, 1)
        changed += 1
    if EVENT_FLOOR_MARKER not in source:
        old = '''        with (output_dir / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in strategy.detector.events:
                stream.write(json.dumps(event, sort_keys=True, default=str) + "\\n")
'''
        new = '''        with (output_dir / "scenario_events.jsonl").open("w", encoding="utf-8") as stream:
            for event in strategy.detector.events:
                stream.write(json.dumps(event, sort_keys=True, default=str) + "\\n")
            # C11_CROSS_COMPLETION_EVENT: preserve an auditable non-empty ledger
            # even when a frozen interval contains no qualifying shock.
            stream.write(json.dumps({
                "type": "CROSS_MARKET_RUN_COMPLETED",
                "observed_ts_ns": evaluation_end_ns,
                "week_id": week_id,
                "detector_events": len(strategy.detector.events),
                "success_claim": False,
            }, sort_keys=True) + "\\n")
'''
        count = source.count(old)
        if count != 1:
            raise SystemExit(f"completion-event runner anchor count={count}")
        source = source.replace(old, new, 1)
        changed += 1
    path.write_text(source, encoding="utf-8")
    return changed


def main() -> None:
    root = Path(__file__).resolve().parent
    changed = patch_detector(root / "cross_market.py")
    changed += patch_runner(root / "run_cross_market_nautilus.py")
    print(f"cross-market runtime fixes applied: {changed}")


if __name__ == "__main__":
    main()
