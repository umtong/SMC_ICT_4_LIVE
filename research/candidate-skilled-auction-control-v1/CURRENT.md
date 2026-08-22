# Current candidate: Structural Auction Control v4

`structural_auction_control_v4.py` is the current integrated policy on this branch.
The preceding v2/v3 files are retained as implementation history, not separate
strategies to combine at account level.

V4 uses one policy stream:

1. public wick channel/trend-line interaction establishes liquidity and direction;
2. rejection, acceptance, defended touch or failed-channel transition owns the episode;
3. a later price/volume response supplies entry evidence;
4. OB/FVG refines location only;
5. stop belongs to the causal sweep/return/wave origin;
6. target is the first inherited structural destination, never an explicit fixed-R,
   percentage, ATR or clock target;
7. one episode registry emits at most one immutable plan while the opportunity lives.

The only account result that matters is the inherited Nautilus continuous account
across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT with one global slot, approximately
3% current-NAV risk at the stop, VIP0 fees and configured slippage. Short smoke and
multi-regime evidence files are raw implementation/decision evidence, not a new
PASS/FAIL or promotion framework.
