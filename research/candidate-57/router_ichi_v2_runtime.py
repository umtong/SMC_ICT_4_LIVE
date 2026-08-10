"""Runtime facade for the public ichiV2-family router.

The implementation owns the source state and causal cross-symbol arbitration.
Indicator helpers required by the reused public-strategy execution shell are
re-exported from the already-tested Picasso adapter.
"""
from router_ichi_v2_impl import *  # noqa: F401,F403
from router_picasso import _aggregate_complete, _atr, _ema, _ema_nan, _sma  # noqa: F401
