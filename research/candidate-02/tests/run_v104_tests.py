#!/usr/bin/env python3
"""Execute the frozen v104 pure-Python tests without third-party pytest."""
from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import sys
import traceback
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class MonkeyPatch:
    def __init__(self) -> None:
        self._undo: list[tuple[Any, str, Any]] = []

    def setattr(self, target: Any, name: str, value: Any) -> None:
        original = getattr(target, name)
        self._undo.append((target, name, original))
        setattr(target, name, value)

    def undo(self) -> None:
        while self._undo:
            target, name, original = self._undo.pop()
            setattr(target, name, original)


def main() -> int:
    # conftest installs the isolated v53_nt_core cost/signal definitions used
    # by the frozen tests. It does not simulate orders, fills, PnL, or NAV.
    load("v104_conftest", HERE / "conftest.py")
    modules = [
        load("v104_test_causality", HERE / "test_v104_causality.py"),
        load("v104_test_activation", HERE / "test_v104_activation_adapter.py"),
    ]
    tests = sorted(
        (module.__name__, name, function)
        for module in modules
        for name, function in vars(module).items()
        if name.startswith("test_") and callable(function)
    )
    if len(tests) != 18:
        raise AssertionError(f"expected 18 frozen v104 tests, found {len(tests)}")

    failed = 0
    for module_name, name, function in tests:
        patch = MonkeyPatch()
        try:
            parameters = list(inspect.signature(function).parameters)
            if not parameters:
                function()
            elif parameters == ["monkeypatch"]:
                function(patch)
            else:
                raise TypeError(f"unsupported test parameters: {parameters}")
            print(f"PASS {module_name}.{name}")
        except Exception:
            failed += 1
            print(f"FAIL {module_name}.{name}")
            traceback.print_exc()
        finally:
            patch.undo()
    print(f"v104 tests: {len(tests) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
