#!/usr/bin/env python3
"""Apply V7 structural-protection support to the native NT strategy.

The patch adds a state-owned protection trigger and after-cost break-even stop.
It does not calculate fills, fees, positions, PnL, or NAV outside
NautilusTrader. The script is idempotent and is executed and tested inside the
pinned NautilusTrader 1.230.0 image before its change is committed.
"""
from __future__ import annotations

import argparse
from pathlib import Path


PROTECTION_HELPER = '''def signal_structural_protection_trigger(signal: dict[str, Any]) -> float | None:
    """Return a validated causal first-objective protection level."""
    raw = signal.get("structural_protection_trigger")
    if raw is None:
        return None
    value = float(raw)
    if not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"invalid structural_protection_trigger={raw!r}")
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
    if PROTECTION_HELPER not in source:
        if marker not in source:
            raise RuntimeError("native equity helper marker missing")
        source = source.replace(marker, PROTECTION_HELPER + marker, 1)

    source = replace_once(
        source,
        '''    target_price: float = 0.0
    lock_price: float = 0.0
    protection_active: bool = False
''',
        '''    target_price: float = 0.0
    lock_price: float = 0.0
    break_even_price: float = 0.0
    structural_protection_active: bool = False
    protection_active: bool = False
''',
        "active leg fields",
    )

    source = replace_once(
        source,
        '''            "invalid_structural_target": 0,
            "entries_submitted": 0,
''',
        '''            "invalid_structural_target": 0,
            "structural_protection_activations": 0,
            "entries_submitted": 0,
''',
        "strategy counters",
    )

    source = replace_once(
        source,
        '''            "target_mode": active.signal.get("target_mode", "EXISTING_NET_R_OBJECTIVE"),
            "protection_active": active.protection_active,
''',
        '''            "target_mode": active.signal.get("target_mode", "EXISTING_NET_R_OBJECTIVE"),
            "structural_protection_trigger": signal_structural_protection_trigger(active.signal),
            "break_even_price": active.break_even_price,
            "structural_protection_active": active.structural_protection_active,
            "protection_active": active.protection_active,
''',
        "position diagnostics",
    )

    source = replace_once(
        source,
        '''            and not active.protection_active
            and not self._evaluation_ending
''',
        '''            and not active.protection_active
            and not active.structural_protection_active
            and not bool(active.signal.get("disable_rapid_failure_reversal", False))
            and not self._evaluation_ending
''',
        "rapid failure guard",
    )

    source = replace_once(
        source,
        '''                "structural_target": signal_structural_target(self.active.signal),
                "target_mode": self.active.signal.get("target_mode", "EXISTING_NET_R_OBJECTIVE"),
''',
        '''                "structural_target": signal_structural_target(self.active.signal),
                "structural_protection_trigger": signal_structural_protection_trigger(self.active.signal),
                "target_mode": self.active.signal.get("target_mode", "EXISTING_NET_R_OBJECTIVE"),
''',
        "entry diagnostics",
    )

    old_refresh = '''    def _refresh_prices(self, active: ActiveLeg) -> None:
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
    new_refresh = '''    def _refresh_prices(self, active: ActiveLeg) -> None:
        fee = self.config.taker_fee_bps / 10_000.0
        reward = active.target_r * active.expected_loss_per_unit + active.maximum_expected_funding_per_unit
        lock_reward = self.config.continuation_protection_lock_r * active.expected_loss_per_unit + active.maximum_expected_funding_per_unit
        structural_target = signal_structural_target(active.signal)
        funding = active.maximum_expected_funding_per_unit
        if active.direction > 0:
            generic_target = (reward + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
            active.lock_price = (lock_reward + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
            active.break_even_price = (funding + active.entry_avg * (1.0 + fee)) / (1.0 - fee)
        else:
            generic_target = (active.entry_avg * (1.0 - fee) - reward) / (1.0 + fee)
            active.lock_price = (active.entry_avg * (1.0 - fee) - lock_reward) / (1.0 + fee)
            active.break_even_price = (active.entry_avg * (1.0 - fee) - funding) / (1.0 + fee)
        active.target_price = structural_target if structural_target is not None else generic_target
'''
    source = replace_once(source, old_refresh, new_refresh, "price refresh")

    manage_marker = '''        active.mfe_net_r = max(active.mfe_net_r, net_r)

        if active.kind == "CONTINUATION" and not active.protection_active and net_r >= self.config.continuation_protection_activate_r:
'''
    manage_replacement = '''        active.mfe_net_r = max(active.mfe_net_r, net_r)

        structural_trigger = signal_structural_protection_trigger(active.signal)
        if (
            structural_trigger is not None
            and not active.structural_protection_active
            and active.direction * (executable - structural_trigger) >= 0.0
        ):
            active.structural_protection_active = True
            active.stop = (
                max(active.stop, active.break_even_price)
                if active.direction > 0
                else min(active.stop, active.break_even_price)
            )
            self.counters["structural_protection_activations"] += 1
            self._emit(
                scenario_id=active.signal["scenario_id"],
                event_type="STRUCTURAL_PROTECTION_ACTIVATED",
                event_time_ns=timestamp_ns,
                observed_time_ns=timestamp_ns,
                previous_state=f"{active.kind}_ACTIVE",
                next_state=f"{active.kind}_STRUCTURALLY_PROTECTED",
                reason_code="FIRST_CAUSAL_LIQUIDITY_OBJECTIVE_REACHED",
                reference_price=active.stop,
                details={
                    "structural_trigger": structural_trigger,
                    "after_cost_break_even": active.break_even_price,
                    "mfe_net_r": net_r,
                },
            )

        if active.kind == "CONTINUATION" and not active.protection_active and net_r >= self.config.continuation_protection_activate_r:
'''
    source = replace_once(
        source,
        manage_marker,
        manage_replacement,
        "structural protection activation",
    )

    old_stops = '''        if active.direction > 0 and executable <= active.stop:
            reason = "PROTECTED_TRAIL" if active.protection_active else "INITIAL_STOP"
            self._submit_exit(reason, timestamp_ns)
        elif active.direction < 0 and executable >= active.stop:
            reason = "PROTECTED_TRAIL" if active.protection_active else "INITIAL_STOP"
            self._submit_exit(reason, timestamp_ns)
'''
    new_stops = '''        if active.direction > 0 and executable <= active.stop:
            reason = (
                "PROTECTED_TRAIL"
                if active.protection_active
                else "STRUCTURAL_PROTECTION"
                if active.structural_protection_active
                else "INITIAL_STOP"
            )
            self._submit_exit(reason, timestamp_ns)
        elif active.direction < 0 and executable >= active.stop:
            reason = (
                "PROTECTED_TRAIL"
                if active.protection_active
                else "STRUCTURAL_PROTECTION"
                if active.structural_protection_active
                else "INITIAL_STOP"
            )
            self._submit_exit(reason, timestamp_ns)
'''
    source = replace_once(source, old_stops, new_stops, "protected stop reasons")

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
