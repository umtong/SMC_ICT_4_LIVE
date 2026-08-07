#!/usr/bin/env python3
"""Run focused v105 tests without adding packages to the common environment."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import traceback

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    path = HERE / "test_v105_logic.py"
    spec = importlib.util.spec_from_file_location("v105_test_logic", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    tests = sorted(
        (name, value)
        for name, value in vars(module).items()
        if name.startswith("test_") and callable(value)
    )
    if len(tests) != 4:
        raise AssertionError(f"expected 4 v105 tests, found {len(tests)}")
    failed = 0
    for name, function in tests:
        try:
            function()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"v105 tests: {len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
