#!/usr/bin/env python3
"""Remove the last float64 path from sparse nanosecond event identifiers."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGETS = (
    HERE / "cross_venue_diagnostics.py",
    HERE / "flow_regime_diagnostics.py",
)

old = '''            result[column] = pd.to_numeric(
                result[column], errors="coerce"
            ).astype("Int64")
'''
new = '''            result[column] = pd.array(
                [
                    int(value) if pd.notna(value) else pd.NA
                    for value in result[column]
                ],
                dtype="Int64",
            )
'''

for path in TARGETS:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"expected exactly one sparse event conversion in {path.name}, found {count}",
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
