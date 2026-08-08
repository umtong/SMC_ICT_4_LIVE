#!/usr/bin/env python3
"""Single ablation: extend only the stop-touch observation horizon.

The baseline preemptive parent-continuation diagnostic requires the local
reversal stop to be touched within the original five-minute signal shock.  This
controlled ablation keeps the parent-state population, immutable stop/target
competition, post-stop three-bar acceptance, continuation geometry, entry delay,
and structural outcome accounting unchanged.  It removes only that same-shock
condition and allows the stop/target race to continue for the already configured
maximum trade-hold horizon.
"""
from __future__ import annotations

import json

import diagnose_parent_initiative_continuation as base


_original_first_reversal_barrier = base.first_reversal_barrier
_extended_stop_touch_minutes = 0


def _extended_first_reversal_barrier(
    minutes,
    *,
    direction: str,
    opened_ns: int,
    stop: float,
    target: float,
    signal_minutes: int,
):
    del signal_minutes
    return _original_first_reversal_barrier(
        minutes,
        direction=direction,
        opened_ns=opened_ns,
        stop=stop,
        target=target,
        signal_minutes=_extended_stop_touch_minutes,
    )


def main() -> int:
    global _extended_stop_touch_minutes
    args = base.build_parser().parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    _extended_stop_touch_minutes = int(config["max_hold_minutes"])
    if _extended_stop_touch_minutes <= int(config["logic"]["signal_minutes"]):
        raise ValueError("extended window must exceed the baseline signal window")
    base.first_reversal_barrier = _extended_first_reversal_barrier
    return base.diagnose(args)


if __name__ == "__main__":
    raise SystemExit(main())
