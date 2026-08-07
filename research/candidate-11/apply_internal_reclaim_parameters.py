#!/usr/bin/env python3
"""Make internal-reclaim semantic thresholds environment configurable.

The patch changes no default.  It exists so a small, predeclared diagnostic
matrix can distinguish missing opportunities from over-strict confirmation.
Position risk, structural targets, fees, fills, and the existing leadership gate
remain unchanged.
"""
from __future__ import annotations

from pathlib import Path

MARKER = "C11_IRX_PARAMETRIC_DEFAULTS"


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return source.replace(old, new, 1)


def apply(root: Path) -> int:
    path = root / "internal_reclaim.py"
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return 0
    source = replace_once(
        source,
        "from math import isfinite, log, sqrt\n",
        "from math import isfinite, log, sqrt\nimport os\n",
        "os import",
    )
    source = replace_once(
        source,
        '''        self.minimum_net_r = max(1.25, float(getattr(config, "min_net_r", 1.25)))
        self.bars: deque[_Bar] = deque(maxlen=3200)
''',
        '''        self.minimum_net_r = max(1.25, float(getattr(config, "min_net_r", 1.25)))
        # C11_IRX_PARAMETRIC_DEFAULTS: environment values are diagnostic-only;
        # omitted variables reproduce the committed semantic defaults exactly.
        self.minimum_target_distance_atr = float(os.getenv("C11_IRX_MIN_TARGET_ATR", "0.75"))
        self.maximum_target_distance_atr = float(os.getenv("C11_IRX_MAX_TARGET_ATR", "12.0"))
        self.minimum_sweep_penetration_atr = float(os.getenv("C11_IRX_MIN_SWEEP_ATR", "0.03"))
        self.maximum_sweep_penetration_atr = float(os.getenv("C11_IRX_MAX_SWEEP_ATR", "1.50"))
        self.minimum_confirmation_body_atr = float(os.getenv("C11_IRX_MIN_BODY_ATR", "0.20"))
        self.minimum_confirmation_location = float(os.getenv("C11_IRX_MIN_LOCATION", "0.65"))
        self.minimum_confirmation_impulse = float(os.getenv("C11_IRX_MIN_IMPULSE", "0.80"))
        self.minimum_relative_volume = float(os.getenv("C11_IRX_MIN_REL_VOLUME", "0.80"))
        self.minimum_buy_fraction = float(os.getenv("C11_IRX_MIN_BUY_FRACTION", "0.52"))
        self.maximum_buy_fraction = float(os.getenv("C11_IRX_MAX_BUY_FRACTION", "0.48"))
        numeric = (
            self.minimum_target_distance_atr,
            self.maximum_target_distance_atr,
            self.minimum_sweep_penetration_atr,
            self.maximum_sweep_penetration_atr,
            self.minimum_confirmation_body_atr,
            self.minimum_confirmation_location,
            self.minimum_confirmation_impulse,
            self.minimum_relative_volume,
            self.minimum_buy_fraction,
            self.maximum_buy_fraction,
        )
        if not all(isfinite(value) for value in numeric):
            raise ValueError("internal-reclaim parameters must be finite")
        if not 0 < self.minimum_target_distance_atr < self.maximum_target_distance_atr:
            raise ValueError("invalid external-target distance interval")
        if not 0 < self.minimum_sweep_penetration_atr < self.maximum_sweep_penetration_atr:
            raise ValueError("invalid internal-sweep interval")
        if not 0 < self.minimum_confirmation_location < 1:
            raise ValueError("confirmation location must be in (0, 1)")
        if not 0 < self.maximum_buy_fraction < 0.5 < self.minimum_buy_fraction < 1:
            raise ValueError("aggressor-flow fractions must straddle one half")
        self.bars: deque[_Bar] = deque(maxlen=3200)
''',
        "parameter state",
    )
    source = source.replace(
        "if not 0.75 * atr <= distance <= 12.0 * atr:",
        "if not self.minimum_target_distance_atr * atr <= distance <= self.maximum_target_distance_atr * atr:",
        1,
    )
    source = source.replace(
        'return fraction >= 0.52 if direction == "LONG" else fraction <= 0.48',
        'return fraction >= self.minimum_buy_fraction if direction == "LONG" else fraction <= self.maximum_buy_fraction',
        1,
    )
    source = source.replace(
        "return bar.volume >= 0.80 * median(prior)",
        "return bar.volume >= self.minimum_relative_volume * median(prior)",
        1,
    )
    source = source.replace(
        "minimum = 0.03 * atr\n        maximum = 1.50 * atr",
        "minimum = self.minimum_sweep_penetration_atr * atr\n        maximum = self.maximum_sweep_penetration_atr * atr",
        1,
    )
    source = source.replace(
        "and body >= 0.20 * atr\n            and location >= 0.65\n            and impulse is not None\n            and impulse >= 0.80",
        "and body >= self.minimum_confirmation_body_atr * atr\n            and location >= self.minimum_confirmation_location\n            and impulse is not None\n            and impulse >= self.minimum_confirmation_impulse",
        1,
    )
    path.write_text(source, encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"internal-reclaim parameter patch applied: {apply(root)}")


if __name__ == "__main__":
    main()
