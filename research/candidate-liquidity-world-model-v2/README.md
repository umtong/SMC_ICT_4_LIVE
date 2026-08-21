# Liquidity World Model V2

This candidate stops treating every OB, FVG, sweep, or derivatives shock as the
same setup.  It joins two independent causal mechanisms in one account:

1. **Controlled liquidity displacement**
   - a pre-existing 15m/60m liquidity boundary is interacted with;
   - the auction fails or is accepted;
   - price breaks local control and leaves a fresh OB/FVG origin;
   - the first return regains aligned initiative;
   - the displacement carries materially elevated, but not blow-off, activity;
   - entry, structural invalidation, and the next pre-existing liquidity
     objective are fixed before future labels.

2. **Regime-aligned derivatives dislocation**
   - futures, index, mark, basis, and OI identify either a forced flush or
     spot-confirmed initiative;
   - continuation must agree with the common 60-minute four-market auction;
   - reversal must repair at a 60-minute liquidity boundary and cease opposing
     the common 15-minute auction.

The four symbols share exactly the same rules.  Symbol identity and absolute
price are never used as decision inputs.

## Account contract

- one global pending/position slot across BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT;
- one action per causal family episode;
- no forced time exit after entry;
- filled trades resolve only at their declared TP or SL;
- position size makes the cost-inclusive declared stop exactly `-1 account R`;
- continuous NAV compounds at 3% account risk per completed trade.

## Why these two families

The first family captures a completed transfer of local control after liquidity
interaction.  The second captures information that candles alone do not contain:
whether the futures move is spot-confirmed initiative or a repairing derivative
dislocation.  Their opportunity sets are different, so frequency grows by adding
independent mechanisms rather than loosening one setup.

`unified_harvest.py` creates the causal action universe.  `route_unified.py`
applies the mechanism rules and routes the single account.  Future bars are used
only for first-passage labels and never for candidate existence or eligibility.
