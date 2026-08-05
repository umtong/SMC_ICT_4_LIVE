#!/usr/bin/env python3
"""Preserve nanosecond event IDs when diagnostic contexts contain sparse rows."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGETS = (
    HERE / "cross_venue_diagnostics.py",
    HERE / "flow_regime_diagnostics.py",
)

for path in TARGETS:
    text = path.read_text(encoding="utf-8")

    replacements = (
        (
            '"probe_time_ns": event.event_time_ns,',
            '"probe_time_ns": str(event.event_time_ns),',
        ),
        (
            '"displacement_time_ns": event.event_time_ns,',
            '"displacement_time_ns": str(event.event_time_ns),',
        ),
        (
            '    return pd.DataFrame(rows.values())\n',
            '    result = pd.DataFrame(rows.values())\n'
            '    for column in ("probe_time_ns", "displacement_time_ns"):\n'
            '        if column in result:\n'
            '            result[column] = pd.to_numeric(\n'
            '                result[column], errors="coerce"\n'
            '            ).astype("Int64")\n'
            '    return result\n',
        ),
    )

    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise SystemExit(
                f"expected exactly one match in {path.name}, found {count}: {old!r}",
            )
        text = text.replace(old, new, 1)

    path.write_text(text, encoding="utf-8")
