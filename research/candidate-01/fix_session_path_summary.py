#!/usr/bin/env python3
"""Make session path summaries robust when no invalid row exists."""

from pathlib import Path

path = Path(__file__).with_name("session_path_diagnostics.py")
text = path.read_text(encoding="utf-8")
old = '''def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    valid = frame.loc[frame.get("valid", False) == True].copy()  # noqa: E712
    values = pd.to_numeric(valid.get("realized_r", pd.Series(dtype=float)), errors="coerce").dropna()
    profits = float(values[values > 0.0].sum())
    losses = abs(float(values[values < 0.0].sum()))
    return {
        "plans": int(len(frame)),
        "valid_paths": int(len(values)),
        "invalid_reasons": frame.loc[frame.get("valid", False) != True, "reason"].value_counts().to_dict(),  # noqa: E712
        "sum_r": float(values.sum()),
'''
new = '''def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    validity = frame.get("valid", pd.Series(False, index=frame.index, dtype=bool))
    valid = frame.loc[validity == True].copy()  # noqa: E712
    invalid_reasons = (
        frame.loc[validity != True, "reason"].value_counts().to_dict()  # noqa: E712
        if "reason" in frame.columns
        else {}
    )
    values = pd.to_numeric(valid.get("realized_r", pd.Series(dtype=float)), errors="coerce").dropna()
    profits = float(values[values > 0.0].sum())
    losses = abs(float(values[values < 0.0].sum()))
    return {
        "plans": int(len(frame)),
        "valid_paths": int(len(values)),
        "invalid_reasons": invalid_reasons,
        "sum_r": float(values.sum()),
'''
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one session summary match, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
