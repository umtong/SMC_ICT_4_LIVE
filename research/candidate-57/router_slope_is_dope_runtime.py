"""Runtime facade for the public Slope-is-Dope router.

The implementation owns the source state and arbitration.  Indicator helpers are
re-exported from the already-tested Picasso adapter so the shared execution shell
can be reused without copying TA code.
"""
from router_slope_is_dope_impl import *  # noqa: F401,F403
from router_picasso import _aggregate_complete, _atr, _ema, _sma  # noqa: F401
