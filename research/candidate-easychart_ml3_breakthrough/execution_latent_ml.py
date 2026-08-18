"""Latent-feature specialization of the integrated learned account strategy."""
from __future__ import annotations

import execution_integrated_ml as _base
from features_latent import FEATURE_NAMES, build_latent_features


# The base strategy deliberately reads these module globals at construction and
# score time.  Specializing them here preserves one execution/account lifecycle
# while changing only the causal evidence schema supplied to the ensemble.
_base.FEATURE_NAMES = FEATURE_NAMES
_base.build_integrated_features = build_latent_features

IntegratedMLRuntimeConfig = _base.IntegratedMLRuntimeConfig
configure_integrated_ml_runtime = _base.configure_integrated_ml_runtime


class EasyChartLatentMLStrategy(_base.EasyChartIntegratedMLStrategy):
    """One-account robust router for non-linear latent auction state."""


StrategyClass = EasyChartLatentMLStrategy
