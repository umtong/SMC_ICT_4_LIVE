#!/usr/bin/env python3
"""Align all auxiliary as-of join keys to UTC nanosecond precision."""

from pathlib import Path

path = Path(__file__).with_name("auxiliary_data.py")
text = path.read_text(encoding="utf-8")
replacements = {
    'result["create_time"] = pd.to_datetime(result["create_time"], utc=True)':
        'result["create_time"] = pd.to_datetime(result["create_time"], utc=True).astype("datetime64[ns, UTC]")',
    '''result["close_time"] = pd.to_datetime(
        pd.to_numeric(result["close_time"], errors="raise"),
        unit="ms",
        utc=True,
    )''':
        '''result["close_time"] = pd.to_datetime(
        pd.to_numeric(result["close_time"], errors="raise"),
        unit="ms",
        utc=True,
    ).astype("datetime64[ns, UTC]")''',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise SystemExit(f"expected one datetime block, found {text.count(old)}: {old[:80]}")
    text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
