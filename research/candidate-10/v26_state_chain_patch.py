"""Apply the verified v26 event-state-chain implementation repair.

No signal, threshold, price, cost, risk, order, target, or evaluation rule is
changed. The patch makes terminal/background event labels reflect the already
entered REACCELERATION_WAIT state after a held retest, and installs regression
tests for the exact failure observed in the first NautilusTrader gate.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path

STATE_BEFORE = "50492a496f053ad2722f64cc14327a1d5a1452a39705392da085ed7304b4631f"
STATE_AFTER = "1db330514409a7d3a61bcf04235ea8100fb6856124fc5326573d9add31ce93ed"
TEST_BEFORE = "6fd755389372f1b615258ba60fb624740651e1a099484d31967c11ccdb9dff1a"
TEST_AFTER = "622c50de2538bcf8efa8657bfa813fc58e81b086f7ce3832ec6d7812ecd634c5"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source match, found {count}")
    return text.replace(old, new, 1)


def apply(source_dir: Path, provenance: Path) -> None:
    state_path = source_dir / "c10_v26_state.py"
    test_path = source_dir / "test_v26_state.py"
    before = {"state": _digest(state_path), "test": _digest(test_path)}
    if before != {"state": STATE_BEFORE, "test": TEST_BEFORE}:
        raise RuntimeError(f"unexpected v26 patch inputs: {before}")

    state = state_path.read_text(encoding="utf-8")
    state = _replace_once(
        state,
        '                previous_state="RETEST_WAIT",\n'
        '                next_state="TARGET_REACHED_WITHOUT_ENTRY",',
        '                previous_state=(\n'
        '                    "REACCELERATION_WAIT" if probe.retest_seen else "RETEST_WAIT"\n'
        '                ),\n'
        '                next_state="TARGET_REACHED_WITHOUT_ENTRY",',
        label="target-before-entry state",
    )
    state = _replace_once(
        state,
        '                previous_state="RETEST_WAIT",\n'
        '                next_state="FAILED_ACCEPTANCE",',
        '                previous_state=(\n'
        '                    "REACCELERATION_WAIT" if probe.retest_seen else "RETEST_WAIT"\n'
        '                ),\n'
        '                next_state="FAILED_ACCEPTANCE",',
        label="failed-acceptance state",
    )
    state = _replace_once(
        state,
        '                previous_state=(\n'
        '                    "RETEST_WAIT" if self.active_probe is not None else "EVENT_COOLDOWN"\n'
        '                ),\n'
        '                next_state=(\n'
        '                    "RETEST_WAIT" if self.active_probe is not None else "EVENT_COOLDOWN"\n'
        '                ),',
        '                previous_state=(\n'
        '                    (\n'
        '                        "REACCELERATION_WAIT"\n'
        '                        if self.active_probe.retest_seen\n'
        '                        else "RETEST_WAIT"\n'
        '                    )\n'
        '                    if self.active_probe is not None\n'
        '                    else "EVENT_COOLDOWN"\n'
        '                ),\n'
        '                next_state=(\n'
        '                    (\n'
        '                        "REACCELERATION_WAIT"\n'
        '                        if self.active_probe.retest_seen\n'
        '                        else "RETEST_WAIT"\n'
        '                    )\n'
        '                    if self.active_probe is not None\n'
        '                    else "EVENT_COOLDOWN"\n'
        '                ),',
        label="background-cross state",
    )
    state_path.write_text(state, encoding="utf-8")

    tests = test_path.read_text(encoding="utf-8")
    marker = '    def test_target_must_preexist_and_be_beyond_break_extreme(self) -> None:\n'
    regression = '''\n    def test_post_retest_terminal_events_preserve_reacceleration_state(self) -> None:\n        machine = self.machine(flow=True)\n        self.start_break(machine)\n        held, plan = machine.on_bar(\n            bar(10, close=100.1, high=100.7, low=99.9, quote=100.0, buy=0.0),\n        )\n        self.assertIsNone(plan)\n        self.assertTrue(any(e.next_state == "REACCELERATION_WAIT" for e in held))\n        events, plan = machine.on_bar(\n            bar(11, close=104.7, high=105.1, low=100.0, quote=100.0, buy=50.0),\n        )\n        self.assertIsNone(plan)\n        terminal = next(\n            e for e in events\n            if e.reason_code == "PREEXISTING_TARGET_REACHED_BEFORE_RETEST_ENTRY"\n        )\n        self.assertEqual(terminal.previous_state, "REACCELERATION_WAIT")\n\n    def test_background_cross_after_retest_keeps_reacceleration_state(self) -> None:\n        machine = self.machine(flow=True)\n        self.start_break(machine)\n        machine.on_bar(\n            bar(10, close=100.1, high=100.7, low=99.9, quote=100.0, buy=0.0),\n        )\n        machine.shelves.append(\n            AcceptedLiquidityShelf(\n                shelf_id="BACKGROUND",\n                side=1,\n                price=100.4,\n                zone=0.1,\n                created_ns=1,\n                formation_start_ns=1,\n                formation_end_ns=1,\n                flow_dominance=0.8,\n                impact_efficiency=0.0,\n            ),\n        )\n        events, plan = machine.on_bar(\n            bar(11, close=100.3, high=100.6, low=100.0, quote=100.0, buy=50.0),\n        )\n        self.assertIsNone(plan)\n        consumed = next(\n            e for e in events\n            if e.reason_code == "TRUE_CROSS_CONSUMED_WHILE_EVENT_SLOT_OCCUPIED"\n        )\n        self.assertEqual(consumed.previous_state, "REACCELERATION_WAIT")\n        self.assertEqual(consumed.next_state, "REACCELERATION_WAIT")\n\n'''
    tests = _replace_once(
        tests,
        marker,
        regression + marker,
        label="state-chain regression tests",
    )
    test_path.write_text(tests, encoding="utf-8")

    after = {"state": _digest(state_path), "test": _digest(test_path)}
    if after != {"state": STATE_AFTER, "test": TEST_AFTER}:
        raise RuntimeError(f"unexpected v26 patch outputs: {after}")
    provenance.parent.mkdir(parents=True, exist_ok=True)
    provenance.write_text(
        json.dumps(
            {
                "classification": "IMPLEMENTATION_ERROR_EVENT_STATE_LABEL_ONLY",
                "before": before,
                "after": after,
                "logic_changed": False,
                "signal_or_parameter_changed": False,
                "evaluation_week_changed": False,
                "repair": (
                    "post-retest terminal and background events now report the "
                    "already-entered REACCELERATION_WAIT state"
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args()
    apply(args.source_dir, args.provenance)


if __name__ == "__main__":
    main()
