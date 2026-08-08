#!/usr/bin/env python3
"""Apply the frozen session-auction parent and temporal contracts."""
from __future__ import annotations

from pathlib import Path
import sys


def replace_exact(text: str, old: str, new: str, *, expected: int, label: str) -> str:
    count = text.count(old)
    if count != expected:
        raise SystemExit(f"{label}: expected {expected} source occurrences, found {count}")
    return text.replace(old, new)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch_vendor.py VENDOR_ROOT")
    root = Path(sys.argv[1])
    base = root / "research/candidate-05/strategy_base.py"
    router = root / "research/candidate-16/strategy.py"

    base_text = base.read_text(encoding="utf-8")
    base_text = replace_exact(
        base_text,
        '                    "SESSION_4H",',
        '                    f"SESSION_4H:{self.current_session_key}",',
        expected=2,
        label="paired session identity",
    )
    base_text = replace_exact(
        base_text,
        '            if pool.kind == kind and abs(pool.level - level) <= tolerance:',
        '            if (\n                pool.kind == kind\n                and pool.source == source\n                and abs(pool.level - level) <= tolerance\n            ):',
        expected=1,
        label="source-preserving pool merge",
    )
    base_text = replace_exact(
        base_text,
        '            pools=list(self.active_pools.values()),',
        '            pools=[\n                pool\n                for pool in self.active_pools.values()\n                if pool.source.startswith("SESSION_4H:")\n            ],',
        expected=1,
        label="session-only execution objective",
    )
    base.write_text(base_text, encoding="utf-8")

    router_text = router.read_text(encoding="utf-8")
    router_text = replace_exact(
        router_text,
        '            if pool.kind == "HIGH"\n            and self.bar_index - pool.created_index >= min_age',
        '            if pool.kind == "HIGH"\n            and pool.source.startswith("SESSION_4H:")\n            and self.bar_index - pool.created_index >= min_age',
        expected=1,
        label="session high parent",
    )
    router_text = replace_exact(
        router_text,
        '            if pool.kind == "LOW"\n            and self.bar_index - pool.created_index >= min_age',
        '            if pool.kind == "LOW"\n            and pool.source.startswith("SESSION_4H:")\n            and self.bar_index - pool.created_index >= min_age',
        expected=1,
        label="session low parent",
    )
    immediate = '''        self.parent_auction = observe(\n            self.parent_auction,\n            self._router_observation(row, direction),\n            self.router_thresholds,\n        )\n        if self.parent_auction.decision is not AuctionDecision.PENDING:\n            self._complete_parent(row)\n'''
    router_text = replace_exact(
        router_text,
        immediate,
        '        # The interaction bar defines the parent event only.\n        # Independent state evidence begins with the next completed bar.\n',
        expected=1,
        label="post-interaction state evidence",
    )
    router_text = replace_exact(
        router_text,
        '            if pool.kind == objective_kind\n            and side * (pool.level - entry) > 0.0',
        '            if pool.kind == objective_kind\n            and pool.source.startswith("SESSION_4H:")\n            and side * (pool.level - entry) > 0.0',
        expected=1,
        label="session-only candidate objective",
    )
    router.write_text(router_text, encoding="utf-8")

    print(base)
    print(router)


if __name__ == "__main__":
    main()
