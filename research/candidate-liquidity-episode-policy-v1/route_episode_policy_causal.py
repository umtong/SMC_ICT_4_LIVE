#!/usr/bin/env python3
"""Run the episode router without full-sample feature preprocessing.

The base router is reused, but feature conversion is deliberately pointwise: no
quantile, mean, variance or category information from later/fresh windows is
allowed to alter an earlier decision. HistGradientBoosting receives finite raw
causal features and performs all fitting inside each chronological training
window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import route_episode_policy as base
from episode_policy_features import FEATURE_COLUMNS


def causal_numeric_features(frame: pd.DataFrame) -> pd.DataFrame:
    output = pd.DataFrame(index=frame.index)
    for column in FEATURE_COLUMNS:
        if column in frame:
            output[column] = pd.to_numeric(frame[column], errors="coerce")
        else:
            output[column] = 0.0
    return output.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype(float)


base._numeric_features = causal_numeric_features

if __name__ == "__main__":
    base.main()
