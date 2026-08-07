#!/usr/bin/env python3
"""Replace V27's kline reader with a header-safe non-mutating parser."""
from __future__ import annotations

import argparse
from pathlib import Path

REPLACEMENT = '''def read_kline_archive(path: Path, market: str) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        members = [name for name in archive.namelist() if name.lower().endswith(".csv")]
        if len(members) != 1:
            raise ValueError(f"expected one CSV in {path}: {members}")
        with archive.open(members[0]) as stream:
            raw = pd.read_csv(stream, header=None, low_memory=False)
    numeric_time = pd.to_numeric(raw.iloc[:, 0], errors="coerce")
    valid = numeric_time.notna()
    filtered = raw.loc[valid]
    timestamps = numeric_time.loc[valid].to_numpy(dtype=np.int64)
    timestamps = np.where(
        timestamps >= 100_000_000_000_000,
        timestamps // 1_000,
        timestamps,
    )

    def values(column: int) -> np.ndarray:
        return pd.to_numeric(
            filtered.iloc[:, column], errors="raise"
        ).to_numpy(dtype=float)

    return pd.DataFrame(
        {
            "open_time_ms": timestamps.astype("int64"),
            f"{market}_open": values(1),
            f"{market}_high": values(2),
            f"{market}_low": values(3),
            f"{market}_close": values(4),
            f"{market}_quote": values(7),
            f"{market}_taker_buy_quote": values(10),
        }
    )


'''


def apply(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    start = source.index("def read_kline_archive(")
    end = source.index("def load_minutes(", start)
    existing = source[start:end]
    if existing == REPLACEMENT:
        return False
    path.write_text(source[:start] + REPLACEMENT + source[end:], encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path("research/candidate-03/derive_nt_lvcfr_v27_signals.py"),
    )
    args = parser.parse_args()
    print(f"V27 parser-v2 patch applied={apply(args.path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
