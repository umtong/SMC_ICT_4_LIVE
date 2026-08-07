#!/usr/bin/env python3
"""Idempotently augment Candidate 11's regional engine with internal reclaim."""
from __future__ import annotations

from pathlib import Path

MARKER = "_Candidate11InternalReclaimAugmentedEngine"

WRAPPER = r'''

# _Candidate11InternalReclaimAugmentedEngine
# The original session-framed engine remains authoritative for FAR/AAC.  This
# wrapper asks an independent internal-reclaim detector for a plan on every
# completed bar, but returns it only when the base engine has no plan.  All
# orders, fills, risk sizing, global arbitration, and NAV remain in the existing
# NautilusTrader runner.
from internal_reclaim import InternalReclaimEngine, is_internal_reclaim_plan

_Candidate11BaseRegionalHandoffAuctionEngine = RegionalHandoffAuctionEngine


class RegionalHandoffAuctionEngine(_Candidate11BaseRegionalHandoffAuctionEngine):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = args[0] if args else kwargs.get("config")
        instrument_id = args[1] if len(args) > 1 else kwargs.get("instrument_id")
        if config is None or instrument_id is None:
            raise ValueError("internal reclaim requires config and instrument_id")
        self._internal_reclaim = InternalReclaimEngine(config, str(instrument_id))
        self._internal_reclaim_owns_lifecycle = False

    @property
    def internal_reclaim_events(self):
        return tuple(self._internal_reclaim.events)

    @property
    def internal_reclaim_skips(self):
        return dict(self._internal_reclaim.skips)

    def on_bar(self, bar):
        base_plan = super().on_bar(bar)
        internal_plan = self._internal_reclaim.on_bar(bar)
        if base_plan is not None:
            if internal_plan is not None:
                self._internal_reclaim.mark_rejected(
                    internal_plan,
                    int(getattr(bar, "ts_ns")),
                    "BASE_SCDAM_PLAN_PRIORITY",
                )
            return base_plan
        return internal_plan

    def mark_submitted(self, plan, *args, **kwargs):
        if is_internal_reclaim_plan(plan):
            self._internal_reclaim_owns_lifecycle = True
            return self._internal_reclaim.mark_submitted(plan, *args, **kwargs)
        return super().mark_submitted(plan, *args, **kwargs)

    def mark_rejected(self, plan, *args, **kwargs):
        if is_internal_reclaim_plan(plan):
            result = self._internal_reclaim.mark_rejected(plan, *args, **kwargs)
            reason = str(args[1]) if len(args) > 1 else str(kwargs.get("reason", "UNKNOWN"))
            skips = getattr(self, "skips", None)
            if skips is not None:
                skips[reason] += 1
            return result
        return super().mark_rejected(plan, *args, **kwargs)

    def mark_entry_filled(self, *args, **kwargs):
        if self._internal_reclaim_owns_lifecycle:
            return self._internal_reclaim.mark_entry_filled(*args, **kwargs)
        return super().mark_entry_filled(*args, **kwargs)

    def mark_trade_terminal(self, *args, **kwargs):
        if self._internal_reclaim_owns_lifecycle:
            try:
                return self._internal_reclaim.mark_trade_terminal(*args, **kwargs)
            finally:
                self._internal_reclaim_owns_lifecycle = False
        return super().mark_trade_terminal(*args, **kwargs)
'''


def apply(root: Path) -> int:
    path = root / "session_engine.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return 0
    if "class RegionalHandoffAuctionEngine" not in source:
        raise SystemExit("regional handoff engine anchor missing")
    path.write_text(source.rstrip() + WRAPPER + "\n", encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"internal-reclaim integration applied: {apply(root)}")


if __name__ == "__main__":
    main()
