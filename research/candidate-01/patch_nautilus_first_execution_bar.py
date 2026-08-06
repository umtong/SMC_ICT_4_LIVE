#!/usr/bin/env python3
"""Use the first fully completed one-minute bar after a signal for entry.

The prior adapter mapped a signal to the first observable minute and then held it
for an additional minute.  This patch uses ``bisect_right`` so activation is
strictly after the signal timestamp, and submits from that completed bar.  The
order still goes through NautilusTrader and never sees an incomplete bar.
"""
from pathlib import Path

PATH = Path(__file__).with_name("nautilus_plan_backtest.py")


def replace_once(source: str, old: str, new: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"expected one match, found {count}: {old[:80]!r}")
    return source.replace(old, new, 1)


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    source = replace_once(
        source,
        "from bisect import bisect_left",
        "from bisect import bisect_right",
    )
    source = replace_once(
        source,
        "        activation_index = bisect_left(\n",
        "        activation_index = bisect_right(\n",
    )
    old = '''            self._time_exit_if_needed(ts_ns)
            self._submit_pending(bar)

            new_plans = list(self.schedule.get(ts_ns, ()))
            if new_plans:
                if (
                    self.portfolio.is_flat(self.config.instrument_id)
                    and self.gate.owner is None
                ):
                    self.pending = new_plans
                else:
                    for plan in new_plans:
                        self._reject(
                            plan,
                            ts_ns=ts_ns,
                            reason="SIGNAL_WHILE_GLOBAL_POSITION_OCCUPIED",
                        )
'''
    new = '''            self._time_exit_if_needed(ts_ns)

            new_plans = list(self.schedule.get(ts_ns, ()))
            if new_plans:
                if (
                    self.portfolio.is_flat(self.config.instrument_id)
                    and self.gate.owner is None
                ):
                    self.pending = new_plans
                else:
                    for plan in new_plans:
                        self._reject(
                            plan,
                            ts_ns=ts_ns,
                            reason="SIGNAL_WHILE_GLOBAL_POSITION_OCCUPIED",
                        )
            # The activation bar is strictly later than the signal timestamp.
            # It is now complete, so submission here is causal and avoids an
            # unintended second full minute of latency.
            self._submit_pending(bar)
'''
    source = replace_once(source, old, new)
    source = replace_once(
        source,
        '            "equal-notional signal mapped to first completed one-minute bar; "\n'
        '            "market bracket evaluated on the following completed one-minute "\n'
        '            "bar; NautilusTrader owns fills and contingent orders"',
        '            "equal-notional signal mapped to the first strictly later "\n'
        '            "completed one-minute bar; market bracket submitted there; "\n'
        '            "NautilusTrader owns fills and contingent orders"',
    )
    source = replace_once(
        source,
        '                    "signal observation mapped to one-minute execution clock; "\n'
        '                    "submission on following completed one-minute bar"',
        '                    "submission on first strictly later completed one-minute "\n'
        '                    "execution bar"',
    )
    PATH.write_text(source, encoding="utf-8")
    print("patched Nautilus entry to the first strictly later completed minute")


if __name__ == "__main__":
    main()
