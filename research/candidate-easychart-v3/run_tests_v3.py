"""Run all v3 tests after applying the structure-policy compatibility repair."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

import structure_runtime_v3  # noqa: F401


def main() -> None:
    root = Path(__file__).resolve().parent
    suite = unittest.defaultTestLoader.discover(
        start_dir=str(root / "tests"),
        pattern="test_*.py",
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
