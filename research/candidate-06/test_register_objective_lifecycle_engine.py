from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from register_objective_lifecycle_engine import (
    ACTIVE_TRADE_ANCHOR,
    ATTEMPT_ANCHOR,
    MANAGE_ANCHOR,
    FINALIZE_ANCHOR,
    SELECTOR_ANCHOR,
    register,
)


class RegistrationTests(unittest.TestCase):
    def test_registration_is_narrow_and_idempotent(self):
        with TemporaryDirectory() as raw:
            root = Path(raw)
            strategy = root / "nautilus_strategy.py"
            execution = root / "nautilus_execution.py"
            strategy.write_text("prefix\n" + SELECTOR_ANCHOR + "suffix\n", encoding="utf-8")
            execution.write_text(
                "prefix\n"
                + ATTEMPT_ANCHOR
                + "body\n"
                + ACTIVE_TRADE_ANCHOR
                + "middle\n"
                + MANAGE_ANCHOR
                + "manage_body\n"
                + FINALIZE_ANCHOR
                + "tail\n",
                encoding="utf-8",
            )
            register(root)
            first_strategy = strategy.read_text(encoding="utf-8")
            first_execution = execution.read_text(encoding="utf-8")
            register(root)
            self.assertEqual(first_strategy, strategy.read_text(encoding="utf-8"))
            self.assertEqual(first_execution, execution.read_text(encoding="utf-8"))
            self.assertIn("OBJECTIVE_LIFECYCLE_ACCEPTANCE_RELAY", first_strategy)
            self.assertIn("validate_pending_signal", first_execution)
            self.assertIn("scenario_details", first_execution)
            self.assertIn("pop_position_exit_for", first_execution)
            self.assertIn("diagnostics_snapshot", first_execution)


if __name__ == "__main__":
    unittest.main()
