#!/usr/bin/env python3
"""Run v62 with a pandas-3-compatible hourly close mask.

The v62 hypothesis, periods, context clocks, lifecycle policy and assessment are
unchanged.  pandas 3 returns an Index for ``DatetimeIndex.minute`` and
``DatetimeIndex.second``; that Index has no ``.eq`` method.  This wrapper only
replaces those two elementwise comparisons with ``==`` before executing the
frozen v62 source.
"""
from __future__ import annotations

from pathlib import Path

SOURCE = Path(__file__).with_name("forced_unwind_context_v62.py")
text = SOURCE.read_text(encoding="utf-8")
old = "mask = close_times.minute.eq(59) & close_times.second.eq(59)"
new = "mask = (close_times.minute == 59) & (close_times.second == 59)"
if text.count(old) != 1:
    raise RuntimeError("v62 contract changed; expected one hourly mask")
text = text.replace(old, new)
exec(
    compile(text, str(SOURCE), "exec"),
    {
        "__name__": "__main__",
        "__file__": str(SOURCE),
        "__package__": None,
    },
)
