#!/usr/bin/env python3
"""Small deterministic checks for the new decision primitive."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from auction_response import (
    accepted_auction_evidence,
    failed_auction_evidence,
    initiative_evidence,
)
from response_event_detection import accepted_signal, failed_signal
from world_model_common import SourceEvent


def _frame(length: int = 190) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=length, freq="min", tz="UTC")
    phase = np.arange(length, dtype=float)
    close = 100.0 + 0.015 * np.sin(phase / 5.0)
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "open": open_,
            "high": np.maximum(open_, close) + 0.10,
            "low": np.minimum(open_, close) - 0.10,
            "close": close,
            "volume": np.full(length, 1_000.0),
            "quote_volume": np.full(length, 100_000.0),
        },
        index=index,
    )


def _set_bar(
    data: pd.DataFrame,
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    activity: float,
) -> None:
    data.iloc[index, data.columns.get_loc("open")] = open_
    data.iloc[index, data.columns.get_loc("high")] = high
    data.iloc[index, data.columns.get_loc("low")] = low
    data.iloc[index, data.columns.get_loc("close")] = close
    data.iloc[index, data.columns.get_loc("quote_volume")] = activity
    data.iloc[index, data.columns.get_loc("volume")] = activity / max(close, 1.0)


def _failed_frame() -> pd.DataFrame:
    data = _frame()
    bars = [
        (100.00, 100.95, 99.95, 100.80, 350_000.0),
        (100.80, 101.75, 100.70, 101.60, 600_000.0),
        (101.60, 102.20, 101.25, 101.40, 750_000.0),
        (101.40, 101.50, 100.75, 100.90, 650_000.0),
        (100.90, 101.00, 100.30, 100.40, 550_000.0),
        (100.40, 100.50, 99.90, 100.00, 450_000.0),
        (100.00, 100.10, 99.65, 99.80, 350_000.0),
    ]
    for offset, bar in enumerate(bars):
        _set_bar(data, 120 + offset, *bar)
    return data


def _accepted_frame() -> pd.DataFrame:
    data = _frame()
    bars = [
        (100.00, 100.90, 99.95, 100.80, 300_000.0),
        (100.80, 101.55, 100.70, 101.40, 500_000.0),
        (101.40, 102.20, 101.30, 102.00, 700_000.0),
        (102.00, 102.10, 101.55, 101.70, 450_000.0),
        (101.70, 101.80, 101.15, 101.30, 380_000.0),
        (101.30, 101.40, 100.90, 101.00, 330_000.0),
        (101.00, 101.30, 100.95, 101.20, 300_000.0),
        (101.20, 101.70, 101.15, 101.60, 340_000.0),
        (101.60, 101.90, 101.50, 101.80, 360_000.0),
    ]
    for offset, bar in enumerate(bars):
        _set_bar(data, 120 + offset, *bar)
    return data


def _assert_same_numeric(left: dict[str, object], right: dict[str, object]) -> None:
    assert left.keys() == right.keys()
    for key in left:
        if isinstance(left[key], str):
            assert left[key] == right[key]
        else:
            assert np.isclose(float(left[key]), float(right[key]), equal_nan=True), key


def main() -> None:
    failed_data = _failed_frame()
    failed = failed_auction_evidence(
        failed_data,
        interaction=120,
        extreme_index=122,
        decision=126,
        source_side="HIGH",
        boundary=100.50,
        event_extreme=102.20,
        atr_price=0.50,
    )
    assert failed["auction_response_kind"] == "OVERSHOOT_SETTLED_INSIDE"
    assert float(failed["penetration_atr"]) > 3.0
    assert float(failed["inside_settle_atr"]) > 1.0
    assert float(failed["reaction_flow_share"]) > 0.0
    assert float(failed["auction_response_score"]) > 0.0

    # Changing bars which did not yet exist at the decision may not change the event.
    future_changed = failed_data.copy()
    for index in range(127, len(future_changed)):
        _set_bar(future_changed, index, 100.0, 110.0, 90.0, 109.0, 9_000_000.0)
    failed_again = failed_auction_evidence(
        future_changed,
        interaction=120,
        extreme_index=122,
        decision=126,
        source_side="HIGH",
        boundary=100.50,
        event_extreme=102.20,
        atr_price=0.50,
    )
    _assert_same_numeric(failed, failed_again)

    accepted_data = _accepted_frame()
    accepted = accepted_auction_evidence(
        accepted_data,
        interaction=120,
        impulse_extreme_index=122,
        decision=128,
        source_side="HIGH",
        boundary=100.50,
        impulse_extreme=102.20,
        pullback_extreme=100.90,
        atr_price=0.50,
    )
    assert accepted["auction_response_kind"] == "BREAK_SETTLED_OUTSIDE"
    assert float(accepted["terminal_outward_atr"]) > 2.0
    assert float(accepted["pullback_hold_margin_atr"]) > 0.0
    assert float(accepted["auction_response_score"]) > 0.0

    initiative = initiative_evidence(
        accepted_data,
        impulse_start=120,
        decision=128,
        side="LONG",
        atr_price=0.50,
    )
    assert initiative["auction_response_kind"] == "INITIATIVE_DISPLACEMENT_MITIGATION"
    assert float(initiative["initiative_move_atr"]) >= 1.9
    assert float(initiative["auction_response_score"]) > 0.0

    source = SourceEvent(
        source_id="SYNTHETIC_HIGH",
        side="HIGH",
        lower=100.40,
        upper=100.50,
        price=100.45,
        observed_index=100,
        interaction_index=120,
        scale=60.0,
        strength=3.0,
        kind="SYNTHETIC_PUBLIC_HIGH",
    )
    atr = np.full(len(failed_data), 0.50)
    failed_node = SimpleNamespace(
        side="HIGH", extreme_index=122, observed_index=126, price=102.20
    )
    signal = failed_signal(failed_data, source, [failed_node], atr)
    assert signal is not None
    assert signal.family == "FAILED_AUCTION_REVERSAL"
    assert float(signal.evidence["auction_response_score"]) > 0.0

    first = SimpleNamespace(
        side="HIGH", extreme_index=122, observed_index=123, price=102.20
    )
    second = SimpleNamespace(
        side="LOW", extreme_index=125, observed_index=128, price=100.90
    )
    continuation = accepted_signal(accepted_data, source, [first, second], atr)
    assert continuation is not None
    assert continuation.family == "ACCEPTED_AUCTION_CONTINUATION"
    assert float(continuation.evidence["auction_response_score"]) > 0.0

    import skilled_liquidity_policy as policy
    import route_skilled_policy as router

    assert callable(policy.generate_symbol)
    assert callable(policy.plan_from_signal)
    assert callable(router.main)
    print("skilled-liquidity-response-v1 self-check: OK")


if __name__ == "__main__":
    main()
