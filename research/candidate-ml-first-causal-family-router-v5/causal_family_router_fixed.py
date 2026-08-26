#!/usr/bin/env python3
"""Execute the v5 family router with its dynamically imported core registered."""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve()
SOURCE_PATH = HERE.with_name("causal_family_router.py")
source = SOURCE_PATH.read_text(encoding="utf-8")
needle = "    module = importlib.util.module_from_spec(spec)\n    spec.loader.exec_module(module)"
replacement = (
    "    module = importlib.util.module_from_spec(spec)\n"
    "    import sys\n"
    "    sys.modules[spec.name] = module\n"
    "    spec.loader.exec_module(module)"
)
if needle not in source:
    raise SystemExit("v5 core import block changed; fixed wrapper cannot apply")
source = source.replace(needle, replacement, 1)
namespace = {"__name__": "__main__", "__file__": str(HERE)}
exec(compile(source, str(SOURCE_PATH), "exec"), namespace, namespace)
