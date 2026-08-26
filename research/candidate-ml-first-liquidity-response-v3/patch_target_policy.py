#!/usr/bin/env python3
"""Patch candidate-4 with one explicit structural target policy.

Variants are deliberately small market-logic alternatives, not a threshold lattice:
- fraction35/40/45: complete a fixed fraction of the nearest target-worthy opposing route;
- first_live_rr1: complete at the first still-live opposing liquidity level at least 1R away.
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

FRACTIONS = {
    "fraction35": 0.35,
    "fraction40": 0.40,
    "fraction45": 0.45,
}


def _patch_fraction(source: Path, fraction: float) -> None:
    part1 = source / "causal_route_research.part1.pyinc"
    part10 = source / "causal_route_research.part10.pyinc"
    text1 = part1.read_text()
    anchor = "TARGET_INSIDE_TICKS = 1\n"
    addition = anchor + f"ROUTE_TARGET_FRACTION = {fraction:.2f}\n"
    if "ROUTE_TARGET_FRACTION" not in text1:
        if anchor not in text1:
            raise RuntimeError("candidate-4 constant anchor not found")
        text1 = text1.replace(anchor, addition, 1)
        part1.write_text(text1)
    text10 = part10.read_text()
    old = "    target = proximal - _sign(side) * TARGET_INSIDE_TICKS * tick\n"
    new = (
        "    full_route_target = proximal - _sign(side) * TARGET_INSIDE_TICKS * tick\n"
        "    target = entry + ROUTE_TARGET_FRACTION * (full_route_target - entry)\n"
        "    target = (math.floor(target / tick) * tick if side == \"LONG\" else math.ceil(target / tick) * tick)\n"
    )
    if "full_route_target = proximal" not in text10:
        if text10.count(old) != 1:
            raise RuntimeError(f"candidate-4 target anchor count was {text10.count(old)}, expected one")
        text10 = text10.replace(old, new, 1)
        part10.write_text(text10)


def _patch_first_live_rr1(source: Path) -> None:
    part9 = source / "causal_route_research.part9.pyinc"
    text = part9.read_text()
    old = '''    target_level: hl.LiquidityLevel | None = None
    proximal = 0.0
    for level, candidate_price in candidates:
        meta = metadata[level.level_id]
        if bool(meta.direction_source) or bool(meta.accumulated) or int(level.timeframe_minutes) >= 15 or int(level.defense_count) >= 2:
            target_level, proximal = level, float(candidate_price)
            break
    if target_level is None:
'''
    new = '''    risk = abs(entry - stop)
    target_level: hl.LiquidityLevel | None = None
    proximal = 0.0
    for level, candidate_price in candidates:
        candidate_target = float(candidate_price) - _sign(side) * TARGET_INSIDE_TICKS * tick
        candidate_rr = abs(candidate_target - entry) / max(risk, EPS)
        if candidate_rr < 1.0:
            continue
        target_level, proximal = level, float(candidate_price)
        break
    if target_level is None:
'''
    if "candidate_rr = abs(candidate_target - entry)" not in text:
        if text.count(old) != 1:
            raise RuntimeError(f"candidate-4 target-worthy loop anchor count was {text.count(old)}, expected one")
        text = text.replace(old, new, 1)
        part9.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--variant", choices=[*FRACTIONS, "first_live_rr1"], required=True)
    args = parser.parse_args()
    source = args.root / "research/candidate-causal-liquidity-route-system"
    if args.variant in FRACTIONS:
        _patch_fraction(source, FRACTIONS[args.variant])
    else:
        _patch_first_live_rr1(source)
    compiled = "".join((source / f"causal_route_research.part{i}.pyinc").read_text() for i in range(1, 12))
    if args.variant in FRACTIONS:
        fraction = FRACTIONS[args.variant]
        if f"ROUTE_TARGET_FRACTION = {fraction:.2f}" not in compiled or "target = entry + ROUTE_TARGET_FRACTION" not in compiled:
            raise RuntimeError("fractional target patch did not materialize")
    elif "candidate_rr = abs(candidate_target - entry)" not in compiled:
        raise RuntimeError("first-live target patch did not materialize")
    compile(compiled, str(source / "causal_route_research.py"), "exec")
    print(f"patched candidate-4 target policy: {args.variant}")


if __name__ == "__main__":
    main()
