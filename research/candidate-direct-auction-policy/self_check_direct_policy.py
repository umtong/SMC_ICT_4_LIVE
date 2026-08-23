from __future__ import annotations

import numpy as np
import pandas as pd

from direct_state_action_harvest import _confirmed_pivot_state, _first_passage


def main() -> None:
    idx = pd.date_range("2025-01-01", periods=9, freq="1min", tz="UTC")
    bars = pd.DataFrame(
        {
            "open": [10, 11, 12, 11, 10, 11, 12, 11, 10],
            "high": [11, 12, 13, 12, 11, 12, 13, 12, 11],
            "low": [9, 10, 11, 10, 9, 10, 11, 10, 9],
            "close": [10.5, 11.5, 12.5, 10.5, 10.5, 11.5, 12.5, 10.5, 10.5],
        },
        index=idx,
    )
    piv = _confirmed_pivot_state(bars, span=2, prefix="x")
    # Pivot high centered at bar 2 may not be visible before bar 4 completes.
    assert np.isnan(piv.iloc[3]["x_pivot_high"])
    assert piv.iloc[4]["x_pivot_high"] == 13

    high = np.array([101.0, 103.0, 104.0])
    low = np.array([99.0, 97.0, 96.0])
    close = np.array([100.0, 100.0, 100.0])
    outcome, index, _ = _first_passage(high, low, close, 0, "LONG", 98.0, 102.0, 100.0, 0.1)
    assert outcome == "AMBIGUOUS_SAME_MINUTE" and index == 1
    print({"status": "ok", "pivot_observed_after_right_span": True, "same_minute": outcome})


if __name__ == "__main__":
    main()
