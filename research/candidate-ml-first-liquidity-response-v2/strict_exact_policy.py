#!/usr/bin/env python3
"""Materialize and execute the compressed strict-causal research policy."""
from __future__ import annotations
import gzip
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
SOURCE_PATH = ROOT / ".bootstrap/candidate-ml-first-liquidity-response-v2/strict_exact_policy.py.gz"
SOURCE = gzip.decompress(SOURCE_PATH.read_bytes()).decode("utf-8")
NAMESPACE = {"__name__": "__main__", "__file__": str(HERE)}
exec(compile(SOURCE, str(HERE), "exec"), NAMESPACE, NAMESPACE)
