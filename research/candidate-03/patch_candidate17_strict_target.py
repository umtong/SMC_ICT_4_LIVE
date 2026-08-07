#!/usr/bin/env python3
"""Replace Candidate 16's failed-FAR target with a strict external objective.

The prior implementation called the ordinary nearest-pool helper from the stop
fill price.  That helper could return the just-swept pool or a duplicate pool at
the failed boundary, causing the new state to terminate immediately because its
objective had already traded.

Candidate 17 changes only the continuation objective definition.  A target must
have existed causally in the pool registry at the stop, remain unconsumed and
unexpired, differ from the parent scenario and failed boundary, and lie strictly
beyond both the stop-time reference and failed boundary.  The nearest such pool
is chosen symmetrically. No fitted distance or return threshold is introduced.
"""
from __future__ import annotations

import argparse
from pathlib import Path

ANCHOR = '''def continuation_direction(side: Side) -> Direction:\n    return Direction.LONG if side == Side.HIGH else Direction.SHORT\n\n\n'''
INSERT = '''def continuation_direction(side: Side) -> Direction:\n    return Direction.LONG if side == Side.HIGH else Direction.SHORT\n\n\ndef strict_failed_far_target(\n    self: CausalAuctionEngine,\n    context: SubmittedFarContext,\n    reference: float,\n) -> Any | None:\n    \"\"\"Return the nearest pre-existing external pool beyond the failed boundary.\"\"\"\n    epsilon = max(1.0, abs(context.boundary), abs(reference)) * 1e-12\n    candidates = []\n    for pool in self.pools:\n        if pool.side != context.pool_side:\n            continue\n        if pool.consumed or self._index > pool.expiry_index or pool.strength < 1:\n            continue\n        if pool.scenario_id == context.parent_scenario_id:\n            continue\n        if abs(pool.level - context.boundary) <= epsilon:\n            continue\n        if context.pool_side == Side.HIGH:\n            if pool.level <= max(reference, context.boundary) + epsilon:\n                continue\n        else:\n            if pool.level >= min(reference, context.boundary) - epsilon:\n                continue\n        candidates.append(pool)\n    if not candidates:\n        return None\n    if context.pool_side == Side.HIGH:\n        return min(candidates, key=lambda pool: (pool.level, pool.scenario_id))\n    return max(candidates, key=lambda pool: (pool.level, pool.scenario_id))\n\n\n'''

OLD_TARGET = '''    target = self._next_pool(context.pool_side, reference, min_strength=1)\n    if target is None:\n        self.skips["FAILED_FAR_NO_SAME_SIDE_EXTERNAL_TARGET"] += 1\n'''
NEW_TARGET = '''    target = strict_failed_far_target(self, context, reference)\n    if target is None:\n        self.skips["FAILED_FAR_NO_STRICT_EXTERNAL_TARGET"] += 1\n'''
OLD_REASON = '''            "FAILED_FAR_NO_SAME_SIDE_EXTERNAL_TARGET",\n'''
NEW_REASON = '''            "FAILED_FAR_NO_STRICT_EXTERNAL_TARGET",\n'''
OLD_KIND = 'SCENARIO_KIND = "FAILED_FAR_ACCEPTANCE_CONTINUATION"\n'
NEW_KIND = 'SCENARIO_KIND = "FAILED_FAR_STRICT_EXTERNAL_ACCEPTANCE_CONTINUATION"\n'


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "def strict_failed_far_target(" in source:
        return False
    for old, label in (
        (ANCHOR, "continuation-direction anchor"),
        (OLD_TARGET, "target-selection block"),
        (OLD_REASON, "no-target reason"),
        (OLD_KIND, "scenario kind"),
    ):
        if source.count(old) != 1:
            raise RuntimeError(f"expected one {label}, found {source.count(old)}")
    source = source.replace(ANCHOR, INSERT, 1)
    source = source.replace(OLD_TARGET, NEW_TARGET, 1)
    source = source.replace(OLD_REASON, NEW_REASON, 1)
    source = source.replace(OLD_KIND, NEW_KIND, 1)
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(f"candidate17 strict target patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
