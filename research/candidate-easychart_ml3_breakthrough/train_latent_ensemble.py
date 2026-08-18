#!/usr/bin/env python3
"""Train the shared portable ensemble using latent auction features."""
from __future__ import annotations

import train_integrated_ensemble as _trainer
from features_latent import (
    FEATURE_CLIP_RANGES,
    FEATURE_DEFAULTS,
    FEATURE_NAMES,
)


_trainer.FEATURE_NAMES = FEATURE_NAMES
_trainer.FEATURE_DEFAULTS = FEATURE_DEFAULTS
_trainer.FEATURE_CLIP_RANGES = FEATURE_CLIP_RANGES


if __name__ == "__main__":
    _trainer.main()
