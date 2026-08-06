#!/usr/bin/env python3
"""Apply the reviewed V6 structural-objective patch to the NT strategy.

This script edits scenario behavior only. It does not add any backtest, fill,
fee, position, or NAV calculation. It is intentionally idempotent so the pinned
runtime workflow can verify and commit the exact strategy change.
"""
from __future__ import annotations

import argparse
from pathlib import Path


HELPER = '''def signal_structural_target(signal: dict[str, Any]) -> float | None:
    """Return a validated causal liquidity objective from a derived schedule."""
    raw = signal.get("structural_target")
    if raw is None:
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"invalid structural_target={raw!r}")
    return value


'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if new in source:
        return source
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one source block, found {count}")
    return source.replace(old, new, 1)


def apply_patch(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    source = original

    marker = "def native_equity_amount(portfolio: Any, venue: Any, currency: Any) -> float:\n"
    if HELPER not in source:
        if marker not in source:
            raise RuntimeError("native_equity_amount insertion marker missing")
        source = source.replace(marker, HELPER + marker, 1)

    source = replace_once(
        source,
        '            "invalid_entry_price": 0,\n            "entries_submitted": 0,',
        '            "invalid_entry_price": 0,\n            "invalid_structural_target": 0,\n            "entries_submitted": 0,',
        "counter",
    )

    source = replace_once(
        source,
        '            "exit_reason": self.exit_reason or "UNKNOWN",\n            "protection_active": active.protection_active,',
        '            "exit_reason": self.exit_reason or "UNKNOWN",\n            "target_price": active.target_price,\n            "target_mode": active.signal.get("target_mode", "EXISTING_NET_R_OBJECTIVE"),\n            "protection_active": active.protection_active,',
        "position diagnostics",
    )

    source = replace_once(
        source,
        '        self._submit_entry(pending, tick)\n\n    def _submit_entry',
        '''        structural_target = signal_structural_target(pending.signal)
        if (
            structural_target is not None
            and pending.direction * (structural_target - executable) <= 0.0
        ):
            self.counters["invalid_structural_target"] += 1
            self._emit(
                scenario_id=pending.signal["scenario_id"],
                event_type="STRUCTURAL_TARGET_ALREADY_REACHED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state="ENTRY_BUFFER" if pending.kind == "CONTINUATION" else "REVERSAL_BUFFER",
                next_state="INVALIDATED",
                reason_code="CAUSAL_LIQUIDITY_OBJECTIVE_NOT_AHEAD_OF_EXECUTABLE_ENTRY",
                reference_price=executable,
                details={"structural_target": structural_target},
            )
            self.pending = None
            self._finalize_episode(timestamp_ns)
            return
        self._submit_entry(pending, tick)

    def _submit_entry''',
        "pre-entry structural target validation",
    )

    source = replace_once(
        source,
        '                "expected_funding_per_unit": expected_funding,\n            },',
        '                "expected_funding_per_unit": expected_funding,\n                "structural_target": signal_structural_target(self.active.signal),\n                "target_mode": self.active.signal.get("target_mode", "EXISTING_NET_R_OBJECTIVE"),\n            },',
        "entry diagnostics",
    )

    old_refresh = '''    def _refresh_prices(self, active: ActiveLeg) -> None:
        fee = self.config.taker_fee_bps / 10_000.0
        reward = active.target_r * active.expected_loss_per_unit + active.maximum_expected_funding_per_unit
        lock_reward = self.config.continuation_protection_lock_r * active.expected_loss_per_unit + active.maximum_expected_funding_per_unit
        if active.direction > 0:
            active.target_price = (reward + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
            active.lock_price = (lock_reward + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
        else:
            active.target_price = (active.entry_avg * (1.0 - fee) - reward) / (1.0 + fee)
            active.lock_price = (active.entry_avg * (1.0 - fee) - lock_reward) / (1.0 + fee)
'''
    new_refresh = '''    def _refresh_prices(self, active: ActiveLeg) -> None:
        fee = self.config.taker_fee_bps / 10_000.0
        reward = active.target_r * active.expected_loss_per_unit + active.maximum_expected_funding_per_unit
        lock_reward = self.config.continuation_protection_lock_r * active.expected_loss_per_unit + active.maximum_expected_funding_per_unit
        structural_target = signal_structural_target(active.signal)
        if active.direction > 0:
            generic_target = (reward + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
            active.lock_price = (lock_reward + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
        else:
            generic_target = (active.entry_avg * (1.0 - fee) - reward) / (1.0 + fee)
            active.lock_price = (active.entry_avg * (1.0 - fee) - lock_reward) / (1.0 + fee)
        active.target_price = structural_target if structural_target is not None else generic_target
'''
    source = replace_once(source, old_refresh, new_refresh, "target price refresh")

    source = replace_once(
        source,
        '''        elif active.direction > 0 and executable >= active.target_price:
            self._submit_exit("TARGET", timestamp_ns)
        elif active.direction < 0 and executable <= active.target_price:
            self._submit_exit("TARGET", timestamp_ns)
''',
        '''        elif active.direction > 0 and executable >= active.target_price:
            reason = "STRUCTURAL_TARGET" if signal_structural_target(active.signal) is not None else "TARGET"
            self._submit_exit(reason, timestamp_ns)
        elif active.direction < 0 and executable <= active.target_price:
            reason = "STRUCTURAL_TARGET" if signal_structural_target(active.signal) is not None else "TARGET"
            self._submit_exit(reason, timestamp_ns)
''',
        "target exit reason",
    )

    if source == original:
        return False
    path.write_text(source, encoding="utf-8")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=Path(__file__).with_name("nt_lvcfr_strategy.py"),
    )
    args = parser.parse_args()
    changed = apply_patch(args.path.resolve())
    print({"path": str(args.path.resolve()), "changed": changed})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
