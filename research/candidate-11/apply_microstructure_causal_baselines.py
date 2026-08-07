#!/usr/bin/env python3
"""Apply causal volatility baselines and robust UTC index conversion."""
from __future__ import annotations

from pathlib import Path

ATR_MARKER = "C11_MICRO_CAUSAL_ATR_BASELINE"
INDEX_MARKER = "C11_MICRO_CHUNKED_UTC_INDEX"

ATR_FUNCTION = r'''    def _atr_60(self) -> float | None:
        # C11_MICRO_CAUSAL_ATR_BASELINE: the current signal second is excluded
        # from its volatility scale.  Five completed one-minute ranges are built
        # from the preceding 301 completed one-second observations.
        if len(self.bars) < 302:
            return None
        sample = list(self.bars)[-302:-1]
        values: list[float] = []
        previous_close = sample[0].close
        for start in range(1, 301, 60):
            part = sample[start:start + 60]
            if len(part) < 60:
                continue
            high = max(bar.high for bar in part)
            low = min(bar.low for bar in part)
            values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
            previous_close = part[-1].close
        atr = sum(values) / len(values) if values else 0.0
        return atr if atr > 0 else None

'''


def replace_method(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    if ATR_MARKER in source:
        return 0
    start = source.find("    def _atr_60(self) -> float | None:\n")
    if start < 0:
        raise SystemExit(f"ATR method missing in {path.name}")
    end = source.find("    def ", start + 8)
    if end < 0:
        raise SystemExit(f"ATR method end missing in {path.name}")
    path.write_text(source[:start] + ATR_FUNCTION + source[end:], encoding="utf-8")
    return 1


def patch_index(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    if INDEX_MARKER in source:
        return 0
    old = '''    result.index = pd.DatetimeIndex(result.index, tz="UTC") + pd.Timedelta(seconds=1)
    return result
'''
    new = '''    # C11_MICRO_CHUNKED_UTC_INDEX
    index = pd.DatetimeIndex(result.index)
    index = index.tz_localize("UTC") if index.tz is None else index.tz_convert("UTC")
    result.index = index + pd.Timedelta(seconds=1)
    return result
'''
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"chunked UTC index anchor count={count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    changed = replace_method(root / "microstructure.py")
    changed += replace_method(root / "microstructure_v2.py")
    changed += patch_index(root / "run_microstructure_nautilus.py")
    print(f"microstructure causal baseline fixes applied: {changed}")


if __name__ == "__main__":
    main()
