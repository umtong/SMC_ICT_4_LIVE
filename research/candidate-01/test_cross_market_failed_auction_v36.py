from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import importlib.util
from pathlib import Path
import sys
import types


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1


@dataclass(frozen=True, slots=True)
class ScenarioPlan:
    scenario_id: str
    response: str
    side: Side
    signal_bar_index: int
    signal_time_ns: int
    stop_price: float
    target_price: float
    confirmation_hold_price: float
    structure_high: float
    structure_low: float
    structure_midpoint: float
    pulse_high: float
    pulse_low: float
    pulse_flow_score: float
    pulse_move_atr: float
    pulse_path_efficiency: float
    pulse_close_location: float
    reason_code: str


agg = types.ModuleType("aggtrade_data")
agg.AggTrade = object
agg.AggTradeDownload = object
agg.iter_downloads = lambda rows: iter(())
sys.modules["aggtrade_data"] = agg
core = types.ModuleType("core")
core.Side = Side
sys.modules["core"] = core
impact = types.ModuleType("impact_regime_probe")
impact.ScenarioPlan = ScenarioPlan
sys.modules["impact_regime_probe"] = impact

path = Path(__file__).with_name("cross_market_failed_auction_v36.py")
spec = importlib.util.spec_from_file_location("v36", path)
assert spec and spec.loader
v36 = importlib.util.module_from_spec(spec)
sys.modules["v36"] = v36
spec.loader.exec_module(v36)


def bar(i: int, *, high: float, low: float, close: float, signed: float):
    start = i * v36.MINUTE_NS
    return v36.MinuteBar(
        start_time_ns=start,
        end_time_ns=start + v36.MINUTE_NS,
        open=close,
        high=high,
        low=low,
        close=close,
        quote_notional=1_000_000.0,
        signed_aggressive_quote=signed,
        trade_count=10,
        first_trade_time_ns=start + 1,
        last_trade_time_ns=start + v36.MINUTE_NS - 1,
    )


def joint(i: int, f, s):
    return v36.JointMinute(
        start_time_ns=i * v36.MINUTE_NS,
        end_time_ns=(i + 1) * v36.MINUTE_NS,
        futures=f,
        spot=s,
    )


def balance_minutes():
    rows = []
    for i in range(30):
        close = 100.4 if i % 2 == 0 else 100.6
        rows.append(
            joint(
                i,
                bar(i, high=101.0, low=100.0, close=close, signed=10_000.0),
                bar(i, high=101.0, low=100.0, close=close, signed=-10_000.0),
            ),
        )
    return rows


def test_spot_unconfirmed_primary_and_control():
    rows = balance_minutes()
    rows.append(
        joint(
            30,
            bar(30, high=101.20, low=100.95, close=101.10, signed=200_000.0),
            bar(30, high=101.03, low=100.90, close=100.98, signed=50_000.0),
        ),
    )
    rows.append(
        joint(
            31,
            bar(31, high=101.25, low=100.75, close=100.90, signed=-250_000.0),
            bar(31, high=101.04, low=100.80, close=100.92, signed=-20_000.0),
        ),
    )
    machine = v36.build_cross_market_plans(rows)
    assert len(machine.primary_plans) == 1
    assert len(machine.control_plans) == 1
    primary = machine.primary_plans[0]
    control = machine.control_plans[0]
    diagnostic = machine.diagnostics[-1]
    assert primary.signal_time_ns == 32 * v36.MINUTE_NS
    assert primary.side is Side.SHORT
    assert primary.confirmation_hold_price == 101.0
    assert primary.target_price == 100.0
    assert primary.stop_price > 101.25
    assert primary.scenario_id.endswith(v36.PRIMARY_SUFFIX)
    assert control.scenario_id.endswith(v36.CONTROL_SUFFIX)
    assert diagnostic.spot_confirmed_before_resolution is False
    assert diagnostic.minutes_to_resolution == 1
    assert diagnostic.full_excursion_rejection_ratio > 1.0
    assert diagnostic.expiry_time_ns == 34 * v36.MINUTE_NS


def test_spot_confirmation_removes_only_primary():
    rows = balance_minutes()
    rows.append(
        joint(
            30,
            bar(30, high=101.20, low=100.95, close=101.10, signed=200_000.0),
            bar(30, high=101.20, low=100.90, close=101.10, signed=100_000.0),
        ),
    )
    rows.append(
        joint(
            31,
            bar(31, high=101.25, low=100.75, close=100.90, signed=-250_000.0),
            bar(31, high=101.22, low=100.80, close=100.92, signed=-20_000.0),
        ),
    )
    machine = v36.build_cross_market_plans(rows)
    assert len(machine.primary_plans) == 0
    assert len(machine.control_plans) == 1
    assert machine.diagnostics[-1].spot_confirmed_before_resolution is True
    assert machine.diagnostics[-1].reason_code.endswith("CONTROL_ONLY")


def test_later_spot_confirmation_is_causal():
    rows = balance_minutes()
    rows.append(
        joint(
            30,
            bar(30, high=101.20, low=100.95, close=101.10, signed=200_000.0),
            bar(30, high=101.03, low=100.90, close=100.98, signed=50_000.0),
        ),
    )
    rows.append(
        joint(
            31,
            bar(31, high=101.25, low=101.01, close=101.12, signed=50_000.0),
            bar(31, high=101.09, low=100.95, close=101.02, signed=20_000.0),
        ),
    )
    rows.append(
        joint(
            32,
            bar(32, high=101.16, low=100.70, close=100.85, signed=-250_000.0),
            bar(32, high=101.04, low=100.85, close=100.92, signed=-20_000.0),
        ),
    )
    machine = v36.build_cross_market_plans(rows)
    assert len(machine.primary_plans) == 0
    assert len(machine.control_plans) == 1
    diagnostic = machine.diagnostics[-1]
    assert diagnostic.spot_confirmation_time_ns == 32 * v36.MINUTE_NS
    assert diagnostic.resolution_time_ns == 33 * v36.MINUTE_NS


def test_lower_sweep_reversal_geometry():
    rows = balance_minutes()
    rows.append(
        joint(
            30,
            bar(30, high=100.05, low=99.75, close=99.85, signed=-200_000.0),
            bar(30, high=100.10, low=99.96, close=100.02, signed=-50_000.0),
        ),
    )
    rows.append(
        joint(
            31,
            bar(31, high=100.30, low=99.70, close=100.10, signed=250_000.0),
            bar(31, high=100.12, low=99.95, close=100.05, signed=20_000.0),
        ),
    )
    machine = v36.build_cross_market_plans(rows)
    assert len(machine.primary_plans) == 1
    plan = machine.primary_plans[0]
    assert plan.side is Side.LONG
    assert plan.confirmation_hold_price == 100.0
    assert plan.target_price == 101.0
    assert plan.stop_price < 99.70


def test_response_window_expiry_allows_new_arm_on_current_minute():
    rows = []
    for i in range(30):
        close = 100.3 if i % 2 == 0 else 100.9
        rows.append(
            joint(
                i,
                bar(i, high=101.0, low=100.0, close=close, signed=10_000.0),
                bar(i, high=101.0, low=100.0, close=close, signed=-10_000.0),
            ),
        )
    rows.append(
        joint(
            30,
            bar(30, high=101.20, low=100.95, close=101.10, signed=200_000.0),
            bar(30, high=101.02, low=100.90, close=100.98, signed=50_000.0),
        ),
    )
    for i in range(31, 34):
        rows.append(
            joint(
                i,
                bar(i, high=101.30, low=101.02, close=101.15, signed=100_000.0),
                bar(i, high=101.03, low=100.90, close=100.99, signed=10_000.0),
            ),
        )
    rows.append(
        joint(
            34,
            bar(34, high=100.20, low=99.70, close=99.80, signed=-200_000.0),
            bar(34, high=100.10, low=99.95, close=100.02, signed=-20_000.0),
        ),
    )
    machine = v36.build_cross_market_plans(rows)
    assert not machine.primary_plans
    assert not machine.control_plans
    assert any(
        row.reason_code == "FUTURES_SWEEP_RESPONSE_WINDOW_EXPIRED"
        for row in machine.diagnostics
    )
    assert len(machine.sweep_events) == 2
    assert machine.sweep_events[-1].outward_side == "SHORT"


def test_bar_availability_is_exact_minute_end():
    row = bar(7, high=101, low=100, close=100.5, signed=0.0)
    assert row.end_time_ns == 8 * v36.MINUTE_NS
    assert row.first_trade_time_ns >= row.start_time_ns
    assert row.last_trade_time_ns < row.end_time_ns


if __name__ == "__main__":
    test_spot_unconfirmed_primary_and_control()
    test_spot_confirmation_removes_only_primary()
    test_later_spot_confirmation_is_causal()
    test_lower_sweep_reversal_geometry()
    test_response_window_expiry_allows_new_arm_on_current_minute()
    test_bar_availability_is_exact_minute_end()
    print("v36 synthetic causal state-machine tests: PASS")
