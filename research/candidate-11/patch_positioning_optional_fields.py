#!/usr/bin/env python3
"""Make sparse Binance metrics ratios optional without discarding OI rows."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATH = ROOT / "analyze_positioning_hypotheses.py"


def replace_once(source: str, old: str, new: str, label: str) -> tuple[str, int]:
    if new in source:
        return source, 0
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1), 1


def main() -> None:
    source = PATH.read_text(encoding="utf-8")
    changed = 0
    source, count = replace_once(
        source,
        """    top_count_ratio: float
    top_sum_ratio: float
    account_ratio: float
    taker_ratio: float
""",
        """    top_count_ratio: float | None
    top_sum_ratio: float | None
    account_ratio: float | None
    taker_ratio: float | None
""",
        "optional metric fields",
    )
    changed += count
    source, count = replace_once(
        source,
        """def _read_csv_member(payload: bytes) -> tuple[str, list[list[str]]]:
""",
        """def _optional_float(value: Any) -> float | None:
    text = str(value).strip()
    if not text:
        return None
    return _float(text)


def _read_csv_member(payload: bytes) -> tuple[str, list[list[str]]]:
""",
        "optional metric parser",
    )
    changed += count
    for old, new, label in (
        ("top_count_ratio=_float(raw[4]),", "top_count_ratio=_optional_float(raw[4]),", "top count parsing"),
        ("top_sum_ratio=_float(raw[5]),", "top_sum_ratio=_optional_float(raw[5]),", "top sum parsing"),
        ("account_ratio=_float(raw[6]),", "account_ratio=_optional_float(raw[6]),", "account parsing"),
        ("taker_ratio=_float(raw[7]),", "taker_ratio=_optional_float(raw[7]),", "taker parsing"),
        (
            "if at_confirmation.taker_ratio > 0:",
            "if at_confirmation.taker_ratio is not None and at_confirmation.taker_ratio > 0:",
            "optional taker use",
        ),
        (
            "if at_confirmation.account_ratio > 0:",
            "if at_confirmation.account_ratio is not None and at_confirmation.account_ratio > 0:",
            "optional account use",
        ),
        (
            "if at_confirmation.top_sum_ratio > 0:",
            "if at_confirmation.top_sum_ratio is not None and at_confirmation.top_sum_ratio > 0:",
            "optional top-position use",
        ),
    ):
        source, count = replace_once(source, old, new, label)
        changed += count
    if changed:
        PATH.write_text(source, encoding="utf-8")
    print(f"positioning optional-field migrations applied: {changed}")


if __name__ == "__main__":
    main()
