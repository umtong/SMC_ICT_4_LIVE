#!/usr/bin/env python3
"""Remove the rejected initial-sweep episode-horizon experiment.

Post-run analysis showed that elapsed time from the first boundary trade-through
is not the causal clock: the auction state updates to later raid extremes. The
experiment changed event ordering, removed a valid SOL episode, and admitted a
new XRP loss. This migration restores the previously validated state machine.
"""
from __future__ import annotations

from pathlib import Path


def replace_if_present(path: Path, old: str, new: str, label: str) -> int:
    source = path.read_text(encoding="utf-8")
    if old not in source:
        return 0
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one rejected-hypothesis anchor, found {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    logic = root / "logic.py"
    tests = root / "test_logic.py"
    missing = [path.name for path in (logic, tests) if not path.is_file()]
    if missing:
        raise SystemExit(f"candidate source is incomplete: {missing}")

    changed = 0
    changed += replace_if_present(
        logic,
        '''\n    @property\n    def causal_episode_bars(self) -> int:\n        """Bars for which a confirmation remains attributable to its sweep."""\n        return self.internal_tf_bars * self.internal_lookback\n''',
        '',
        "causal episode property",
    )
    changed += replace_if_present(
        logic,
        '''        if a.elapsed > self.config.causal_episode_bars and bar.ts_ns >= a.pool.trigger_start_ts_ns:\n            self._terminal(a, bar, "CAUSAL_EPISODE_MEMORY_EXPIRED")\n            return None\n''',
        '',
        "causal episode terminal",
    )

    source = tests.read_text(encoding="utf-8")
    start = source.find("\n\nclass TestCausalEpisodeMemory(unittest.TestCase):")
    if start >= 0:
        end = source.find('\n\nif __name__ == "__main__":', start)
        if end < 0:
            raise SystemExit("rejected causal episode test has no terminal anchor")
        tests.write_text(source[:start] + source[end:], encoding="utf-8")
        changed += 1

    print(f"rejected causal episode-memory changes removed: {changed}")


if __name__ == "__main__":
    main()
