#!/usr/bin/env python3
"""Compile the research source fragments as one module.

The fragments keep this large first diagnostic implementation reviewable and allow a
single atomic Git data commit through the connected repository interface.  They are
concatenated byte-for-byte before execution.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = "".join(
    (ROOT / f"causal_route_research.part{part}.pyinc").read_text()
    for part in range(1, 8)
)
NAMESPACE = {"__name__": "__main__", "__file__": str(Path(__file__).resolve())}
exec(compile(SOURCE, NAMESPACE["__file__"], "exec"), NAMESPACE, NAMESPACE)
