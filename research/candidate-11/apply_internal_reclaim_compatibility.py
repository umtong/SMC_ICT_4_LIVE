#!/usr/bin/env python3
"""Harden IRX against historical Candidate 11 plan-interface aliases."""
from __future__ import annotations

from pathlib import Path

MARKER = "C11_IRX_COMPATIBILITY_V3"


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
        "from logic import Direction, Scenario, TradePlan\n",
        '''# C11_IRX_COMPATIBILITY_V3
import logic as _logic
Direction = _logic.Direction
TradePlan = _logic.TradePlan
Scenario = getattr(_logic, "Scenario", getattr(_logic, "ScenarioType", None))
if Scenario is None:
    raise ImportError("Candidate 11 scenario enum is unavailable")
''',
        "logic imports",
    )
    source = replace_once(
        source,
        '''        if field.default is not MISSING or field.default_factory is not MISSING:
            continue
        raise TypeError(f"unsupported required TradePlan field: {field.name}")
''',
        '''        if field.default is not MISSING or field.default_factory is not MISSING:
            continue
        # Required fields introduced by the current project TradePlan are
        # deterministic economic fields already implied by this costed plan.
        # They do not add a new alpha filter or alter the fixed risk fraction.
        if field.name == "atr":
            details = canonical.get("details") or {}
            fallback = abs(float(canonical["expected_entry"]) - float(canonical["stop_price"]))
            kwargs[field.name] = float(values.get("atr", details.get("atr", fallback)))
            continue
        if field.name == "gain_per_unit":
            kwargs[field.name] = float(values.get(
                "gain_per_unit",
                abs(float(canonical["target_price"]) - float(canonical["expected_entry"])),
            ))
            continue
        if field.name == "reason_code":
            kwargs[field.name] = str(values.get("reason_code", "INTERNAL_RECLAIM_CONFIRMED"))
            continue
        # Historical source revisions briefly exposed zone/time metadata as
        # top-level required fields. They are deterministic aliases of the
        # already costed plan, never additional alpha inputs.
        if field.name in {"entry_zone_low", "zone_low"}:
            kwargs[field.name] = float(canonical["expected_entry"])
            continue
        if field.name in {"entry_zone_high", "zone_high"}:
            kwargs[field.name] = float(canonical["expected_entry"])
            continue
        if field.name in {"created_ts_ns", "event_ts_ns", "confirmation_ts_ns", "ts_ns"}:
            kwargs[field.name] = int(canonical["observed_ts_ns"])
            continue
        if field.name in {"entry_expiry_bars", "expiry_bars"}:
            kwargs[field.name] = 8
            continue
        if field.name in {"source", "plan_source"}:
            kwargs[field.name] = SOURCE
            continue
        raise TypeError(f"unsupported required TradePlan field: {field.name}")
''',
        "required field compatibility",
    )
    # Supply exact current-contract values from the live IRX calculation when
    # the source emits a plan. The adapter fallbacks above remain only for
    # regression fixtures and older committed snapshots.
    source = replace_once(
        source,
        '''            "target_price": target,
            "loss_per_unit": loss_per_unit,
            "net_r": net_r,
''',
        '''            "target_price": target,
            "atr": atr,
            "loss_per_unit": loss_per_unit,
            "gain_per_unit": abs(target - entry),
            "net_r": net_r,
            "reason_code": "INTERNAL_RECLAIM_CONFIRMED",
''',
        "current TradePlan economics",
    )
    path.write_text(source, encoding="utf-8")
    return 1


def main() -> None:
    root = Path(__file__).resolve().parent
    print(f"internal-reclaim compatibility patch applied: {apply(root)}")


if __name__ == "__main__":
    main()
