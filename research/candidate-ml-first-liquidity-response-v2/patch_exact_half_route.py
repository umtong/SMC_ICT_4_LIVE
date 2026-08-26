#!/usr/bin/env python3
"""Patch a checked-out candidate-4 runtime to use an exact 50% structural-route target."""
from __future__ import annotations
import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    source = args.root / "research/candidate-causal-liquidity-route-system"
    part1 = source / "causal_route_research.part1.pyinc"
    part10 = source / "causal_route_research.part10.pyinc"
    text1 = part1.read_text()
    anchor = "TARGET_INSIDE_TICKS = 1\n"
    addition = anchor + "ROUTE_TARGET_FRACTION = 0.50\n"
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
    compiled = "".join((source / f"causal_route_research.part{i}.pyinc").read_text() for i in range(1, 12))
    if "ROUTE_TARGET_FRACTION = 0.50" not in compiled or "target = entry + ROUTE_TARGET_FRACTION" not in compiled:
        raise RuntimeError("exact half-route patch did not materialize")
    compile(compiled, str(source / "causal_route_research.py"), "exec")
    print("patched candidate-4 exact structural target to route fraction 0.50")


if __name__ == "__main__":
    main()
