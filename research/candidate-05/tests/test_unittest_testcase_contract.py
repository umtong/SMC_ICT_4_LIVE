from __future__ import annotations

import ast
from pathlib import Path
import unittest


class UnittestTestCaseContractTest(unittest.TestCase):
    """Prevent helper methods from silently overriding unittest.TestCase.run."""

    def test_no_testcase_class_redefines_framework_run_method(self) -> None:
        test_root = Path(__file__).resolve().parent
        violations: list[str] = []
        for path in sorted(test_root.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {ast.unparse(base).split(".")[-1] for base in node.bases}
                if "TestCase" not in bases:
                    continue
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "run":
                        violations.append(f"{path.name}:{node.name}.run")
        self.assertEqual(
            violations,
            [],
            "TestCase.run is a unittest lifecycle method; name helpers make_run instead: "
            + ", ".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
