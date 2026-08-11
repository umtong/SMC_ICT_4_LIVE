#!/usr/bin/env python3
"""Run v64 with the missing JSON serializer restored.

The v64 strategy rules, data, periods, operator-precedence comparison,
intrabar paths, episode accounting and arbitration are unchanged.  The first
workflow failed only while serializing pandas/numpy timestamp and scalar types
because ``_json_default`` was referenced but not defined.
"""
from __future__ import annotations

from pathlib import Path

SOURCE = Path(__file__).with_name("picasso_precedence_anatomy_v64.py")
text = SOURCE.read_text(encoding="utf-8")
needle = "\ndef _sha256_file(path: Path) -> str:\n"
serializer = '''
def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    raise TypeError(type(value))

'''
if text.count(needle) != 1:
    raise RuntimeError("v64 contract changed; serializer insertion point missing")
text = text.replace(needle, "\n" + serializer + "def _sha256_file(path: Path) -> str:\n", 1)
exec(
    compile(text, str(SOURCE), "exec"),
    {
        "__name__": "__main__",
        "__file__": str(SOURCE),
        "__package__": None,
    },
)
