# Core FAR path diagnostic

This directory is deliberately classified as `TEMPORARY_TEST`.

It reopens the already-consumed D1-D3 development data only to diagnose the
rejected `SCDAM_CORE/FAR` scenario. It does not generate alternative fills or
NAV. It maps the recorded NautilusTrader positions back to their submitted
plans, reloads the exact Binance one-minute bars, and measures:

- favorable/adverse excursion in structural R;
- progress toward the frozen target;
- reclaimed pool and displacement-zone failure timing;
- persistence of post-entry peer-market support;
- outcome by frozen stop model.

The 0.25R and 0.5R labels are descriptive path buckets, not proposed entry
filters. A diagnostic result cannot advance a candidate, delete losses or claim
alpha. It may only identify which market-state assumption a separately defined
candidate would need to replace.
