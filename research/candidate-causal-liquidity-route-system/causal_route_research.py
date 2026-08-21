#!/usr/bin/env python3
"""Compile the causal liquidity-route research fragments as one module.

The first seven fragments preserve the reusable v1 implementation.  Fragments 8-11
form the structural v2 overlay, replacing the weak decision functions while retaining
causal liquidity construction, data loading, diagnostics, and chart generation.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = "".join(
    (ROOT / f"causal_route_research.part{part}.pyinc").read_text()
    for part in range(1, 12)
)
NAMESPACE = {"__name__": "__main__", "__file__": str(Path(__file__).resolve())}
exec(compile(SOURCE, NAMESPACE["__file__"], "exec"), NAMESPACE, NAMESPACE)
