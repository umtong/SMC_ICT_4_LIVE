#!/usr/bin/env python3
"""Patch the native strategy to reject structural targets below net break-even."""
from __future__ import annotations

import argparse
from pathlib import Path

MARKER = "STRUCTURAL_OBJECTIVE_DOES_NOT_CLEAR_COSTS"


def apply_patch(strategy_path: Path) -> bool:
    source = strategy_path.read_text(encoding="utf-8")
    if MARKER in source:
        return False

    import_anchor = "from nautilus_trader.trading.strategy import Strategy\n"
    import_line = (
        "from nt_lvcfr_cost_viability import "
        "expected_structural_target_net_per_unit\n"
    )
    if import_anchor not in source:
        raise RuntimeError("strategy import anchor not found")
    source = source.replace(
        import_anchor,
        import_anchor + "\n" + import_line,
        1,
    )

    counter_anchor = '            "invalid_structural_target": 0,\n'
    if counter_anchor not in source:
        raise RuntimeError("strategy counter anchor not found")
    source = source.replace(
        counter_anchor,
        counter_anchor + '            "unprofitable_structural_target": 0,\n',
        1,
    )

    submit_anchor = "        self._submit_entry(pending, tick)\n"
    if source.count(submit_anchor) != 1:
        raise RuntimeError("strategy submit anchor is not unique")
    viability_block = '''        if structural_target is not None:
            fee = self.config.taker_fee_bps / 10_000.0
            hold = (
                self.config.continuation_max_holding_minutes
                if pending.kind == "CONTINUATION"
                else self.config.reversal_max_holding_minutes
            )
            expected_funding = expected_funding_debit_per_unit(
                entry_price=executable,
                direction=pending.direction,
                funding_rate=self.latest_funding_rate,
                entry_time_ns=timestamp_ns,
                max_holding_minutes=hold,
                next_funding_ns=self.next_funding_ns,
                funding_interval_minutes=self.latest_funding_interval_minutes,
            )
            target_net = expected_structural_target_net_per_unit(
                entry_price=executable,
                target_price=structural_target,
                direction=pending.direction,
                fee_fraction=fee,
                adverse_funding_per_unit=expected_funding,
            )
            if not math.isfinite(target_net) or target_net <= 0.0:
                self.counters["unprofitable_structural_target"] += 1
                self._emit(
                    scenario_id=pending.signal["scenario_id"],
                    event_type="STRUCTURAL_TARGET_COST_INFEASIBLE",
                    event_time_ns=timestamp_ns,
                    observed_time_ns=timestamp_ns,
                    previous_state=(
                        "ENTRY_BUFFER"
                        if pending.kind == "CONTINUATION"
                        else "REVERSAL_BUFFER"
                    ),
                    next_state="INVALIDATED",
                    reason_code="STRUCTURAL_OBJECTIVE_DOES_NOT_CLEAR_COSTS",
                    reference_price=executable,
                    details={
                        "structural_target": structural_target,
                        "expected_target_net_per_unit": target_net,
                        "taker_fee_fraction": fee,
                        "expected_adverse_funding_per_unit": expected_funding,
                    },
                )
                self.pending = None
                self._finalize_episode(timestamp_ns)
                return
        self._submit_entry(pending, tick)
'''
    source = source.replace(submit_anchor, viability_block, 1)
    strategy_path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "strategy_path",
        type=Path,
        nargs="?",
        default=Path(__file__).with_name("nt_lvcfr_strategy.py"),
    )
    args = parser.parse_args()
    changed = apply_patch(args.strategy_path.resolve())
    print(f"cost viability patch applied={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
