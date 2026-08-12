from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import unittest

from trade_semantic_audit import (
    Bar,
    _paths,
    _position_event,
    audit_setup_lifecycle,
    scan_setup_path,
)


def setup(**overrides):
    values = {
        "side": 1,
        "entry": 100.0,
        "stop": 99.0,
        "initial_target": 102.0,
        "target_mode": "FIXED_STRUCTURE",
        "observed_time_ns": 0,
        "valid_until_ns": 10_000,
    }
    values.update(overrides)
    return values


class TestTradeSemanticAudit(unittest.TestCase):
    def test_entry_is_ambiguous_when_one_path_consumes_target_first(self):
        bar = Bar("BTCUSDT", 1, 2, 101.0, 103.0, 98.0, 100.5)
        audit = audit_setup_lifecycle(setup(), [bar])
        self.assertEqual(audit.classification, "PATH_AMBIGUOUS")
        self.assertEqual(
            {result.event for result in audit.path_results},
            {"TARGET_CONSUMED", "ENTRY_THEN_STOP"},
        )

    def test_entry_and_target_are_robust_under_both_paths(self):
        bar = Bar("BTCUSDT", 1, 2, 101.0, 101.5, 99.5, 102.5)
        audit = audit_setup_lifecycle(setup(), [bar])
        self.assertEqual(audit.classification, "ROBUST")
        self.assertEqual(audit.event, "ENTRY_THEN_TARGET")

    def test_gap_through_stop_invalidates_before_entry(self):
        bar = Bar("BTCUSDT", 1, 2, 98.0, 100.0, 97.0, 99.0)
        results = [
            scan_setup_path(setup(), bar, name, path)
            for name, path in _paths(bar)
        ]
        self.assertEqual(
            {result.event for result in results},
            {"STOP_INVALID_AT_OPEN"},
        )

    def test_open_position_bar_can_be_path_ambiguous(self):
        bar = Bar("BTCUSDT", 1, 2, 100.0, 103.0, 98.0, 100.5)
        events = {
            _position_event(
                side=1,
                stop=99.0,
                target=102.0,
                path=path,
            )[0]
            for _, path in _paths(bar)
        }
        self.assertEqual(events, {"STOP", "TARGET"})

    def test_short_geometry_is_symmetric(self):
        short = setup(side=-1, entry=100.0, stop=101.0, initial_target=98.0)
        bar = Bar("BTCUSDT", 1, 2, 99.0, 100.5, 98.5, 97.5)
        audit = audit_setup_lifecycle(short, [bar])
        self.assertEqual(audit.classification, "ROBUST")
        self.assertEqual(audit.event, "ENTRY_THEN_TARGET")


if __name__ == "__main__":
    unittest.main()
