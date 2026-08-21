# Skilled Auction Control v1

This candidate is not another threshold revision of the liquidity-auction
lattice.  It uses the existing causal structure, Binance aggressor-flow data,
NautilusTrader execution and account accounting, but replaces the decision
policy with two mechanism owners.

## Channel rejection owner

A pre-existing 15-minute wick channel must be in the correct four-point phase.
A five-minute candle sweeps the projected edge and reclaims it.  The next
completed five-minute candle must still hold inside.  Entry is allowed only on
the first later one-minute OB/FVG response, or on a coherent aggressor-flow
substitute when no visual footprint exists.  The stop contains the complete
sweep and latest confirmed decision swing.  The full target is the first causal
5m/15m obstacle or channel objective.

## Channel acceptance owner

A five-minute body closes outside a pre-existing channel, the next completed
five-minute candle holds outside, and the first detached one-minute return
responds.  The direction must agree with the active liquidity-delivery draw.
The immutable stop contains the projected edge, completed return wick and
breakout-wave origin.  The full target is the nearest unspent opposing structure
or channel extension selected before entry.

A simultaneous overlapping episode belongs to acceptance.  A channel
interaction already emitted by either owner cannot be traded again under a new
label.  There is no plan score, trained router, trade quota, partial entry,
partial exit, moving stop, moving target, time exit or PnL-dependent fallback.

The shared trading contract remains one account, one global position across
BTCUSDT/ETHUSDT/SOLUSDT/XRPUSDT, gross planned RR of at least 1.0, and quantity
sized so the immutable stop risks approximately 3% of current NAV.
