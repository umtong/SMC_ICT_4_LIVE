#!/usr/bin/env python3
"""Expose the v5 deterministic family policy without executing its CLI main."""
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
    raise RuntimeError("v5 load_core implementation changed; module wrapper cannot apply")
source = source.replace(needle, replacement, 1)
namespace = {
    "__name__": "candidate_ml_first_causal_family_module_impl",
    "__file__": str(SOURCE_PATH),
}
exec(compile(source, str(SOURCE_PATH), "exec"), namespace, namespace)
for name, value in namespace.items():
    if not name.startswith("__"):
        globals()[name] = value
