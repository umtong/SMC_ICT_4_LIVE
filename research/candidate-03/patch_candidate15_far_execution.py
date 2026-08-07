#!/usr/bin/env python3
"""Apply one explicit Candidate 15 FAR execution ablation.

The detector, FAR direction, external liquidity target, semantic market gate,
fees, 3% NAV loss budget, global slot and Nautilus execution remain unchanged.
Only the post-confirmation execution thesis changes:

``SWEEP_MARKET``
    Enter immediately only when the original sweep-extreme invalidation retains
    the frozen costed R.  Remove Candidate 14's narrower displacement-void stop
    fallback.  Otherwise retain the original passive execution-void limit.

``PASSIVE_RETEST``
    Always retain the original passive execution-void limit, original sweep
    invalidation and structural target for the frozen 60-minute structure
    expiry.  This tests whether Candidate 14's market reclassification chased
    confirmation rather than buying/selling the defended retest.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re

SWEEP_MARKET = '''def _far_plan(
    self: CausalAuctionEngine,
    a: Auction,
    confirmation_bar: BarObs,
    reason: str,
) -> TradePlan | None:
    inherited = BASE_COSTED_LIMIT_PLAN(self, a, confirmation_bar, reason)
    if inherited is None:
        return None

    expire_ts_ns, structural_minutes = _structure_expiry(self, confirmation_bar.ts_ns)
    passive_details = dict(inherited.details)
    passive_details.update(
        {
            "entry_model": "EXECUTION_VOID_RETEST_IF_SWEEP_MARKET_R_FAILS",
            "stop_model": "SWEEP_EXTREME_INVALIDATION",
            "original_sweep_stop": inherited.stop_price,
            "entry_expiry_structure_minutes": structural_minutes,
            "candidate15_execution_variant": "SWEEP_MARKET",
        }
    )
    passive = replace(inherited, expire_ts_ns=expire_ts_ns, details=passive_details)

    entry = confirmation_bar.close
    original_stop = inherited.stop_price
    immediate, _risk, loss, net_gain, net_r = qualify_market_entry(
        direction=a.direction,
        entry=entry,
        stop=original_stop,
        target=inherited.target_price,
        atr=a.atr,
        min_stop_atr=self.config.min_stop_atr,
        min_net_r=self.config.min_net_r,
        taker_rate=self.config.effective_taker_rate,
        target_maker_rate=self.config.effective_maker_rate,
    )
    if immediate:
        plan = _market_plan(
            passive=passive,
            entry=entry,
            stop=original_stop,
            loss=loss,
            net_gain=net_gain,
            net_r=net_r,
            reason_code="FAR_CONFIRMED_SWEEP_INVALIDATION_MARKET",
            entry_model="CONFIRMED_RECLAIM_DISPLACEMENT_MARKET",
            stop_model="SWEEP_EXTREME_INVALIDATION",
            extra_details={
                "original_sweep_stop": original_stop,
                "candidate15_execution_variant": "SWEEP_MARKET",
            },
        )
        _record_market_reclassification(
            self,
            plan,
            original_passive_entry=inherited.expected_entry,
            original_sweep_stop=original_stop,
        )
        return plan

    _amend_last_plan_event(
        self,
        passive.scenario_id,
        {
            "expire_ts_ns": expire_ts_ns,
            "entry_expiry_structure_minutes": structural_minutes,
            "market_entry_rejected_net_r": net_r,
            "candidate15_execution_variant": "SWEEP_MARKET",
        },
    )
    return passive
'''

PASSIVE_RETEST = '''def _far_plan(
    self: CausalAuctionEngine,
    a: Auction,
    confirmation_bar: BarObs,
    reason: str,
) -> TradePlan | None:
    inherited = BASE_COSTED_LIMIT_PLAN(self, a, confirmation_bar, reason)
    if inherited is None:
        return None

    expire_ts_ns, structural_minutes = _structure_expiry(self, confirmation_bar.ts_ns)
    details = dict(inherited.details)
    details.update(
        {
            "entry_model": "DEFENDED_EXECUTION_VOID_RETEST",
            "stop_model": "SWEEP_EXTREME_INVALIDATION",
            "original_sweep_stop": inherited.stop_price,
            "entry_expiry_structure_minutes": structural_minutes,
            "candidate15_execution_variant": "PASSIVE_RETEST",
        }
    )
    passive = replace(inherited, expire_ts_ns=expire_ts_ns, details=details)
    _amend_last_plan_event(
        self,
        passive.scenario_id,
        {
            "expire_ts_ns": expire_ts_ns,
            "entry_expiry_structure_minutes": structural_minutes,
            "entry_order_type": "LIMIT",
            "entry_post_only": True,
            "expected_entry": passive.expected_entry,
            "stop": passive.stop_price,
            "net_r": passive.net_r,
            "entry_cost_assumption": "MAKER",
            "candidate15_execution_variant": "PASSIVE_RETEST",
        },
    )
    return passive
'''

PATTERN = re.compile(r"def _far_plan\(\n.*?\n\ndef _aac_plan\(", re.DOTALL)


def apply(path: Path, variant: str) -> bool:
    source = path.read_text(encoding="utf-8")
    marker = f'"candidate15_execution_variant": "{variant}"'
    if marker in source:
        return False
    replacement = SWEEP_MARKET if variant == "SWEEP_MARKET" else PASSIVE_RETEST
    replacement += "\n\ndef _aac_plan("
    updated, count = PATTERN.subn(replacement, source, count=1)
    if count != 1:
        raise RuntimeError(f"expected one _far_plan block, replaced {count}")
    path.write_text(updated, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("variant", choices=("SWEEP_MARKET", "PASSIVE_RETEST"))
    args = parser.parse_args()
    print(f"candidate15 FAR execution patch applied={apply(args.path, args.variant)} variant={args.variant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
