# Candidate 06 BAVR Terminal Report

## Terminal decision

`bavr_balanced_value_full` is discarded as a first-week logic failure.  The
NautilusTrader run, checksum-verified Binance USD-M aggregate-trade data, profile
count, minute context and all causality tests were valid.  The failure is not an
implementation or data classification.

## Fixed first BTC week

- week: `2024-02-26` UTC
- aggregate trades: `13,867,170`
- completed one-minute contexts: `10,080`
- completed 15-minute profiles: `672`
- full variant trades: `0`
- full geometric daily NAV growth: `0.0`

The full state machine created 21 adjacent shared-value balance contexts and 25
aggressive value-edge excursions.  Fourteen excursions developed two outside
accepted closes and were correctly reset as price discovery.  Seven reclaimed
value, but none completed the required independent response with a still-valid
POC or opposite-value objective.  The remaining episodes expired or had their
objective consumed before entry.

## Controlled ablations

Removing only the aggregate-trade distribution-quality requirement still
produced zero trades.  Replacing aggregate-trade minute flow with the kline flow
proxy also produced zero trades.  Removing the balance requirement produced ten
trades and four wins, but cost-after geometric daily growth was approximately
`-0.9714%`, profit factor approximately `0.6010`, and maximum drawdown
approximately `9.58%`.

## Largest performance factor

The market frequently continued price discovery after leaving a completed value
area.  Forcing every profile-edge sweep into a reversion thesis was negative;
requiring a genuine balanced-auction context removed those false reversions but
also removed the independent entry opportunity.  Therefore the BAVR balance
classifier worked as a selective no-trade mechanism, not as an alpha source.

## Working components retained

- checksum-verified Binance aggregate-trade ingestion;
- prior-only completed minute and profile timestamps;
- true volume-at-price POC, VAL and VAH rather than candle approximations;
- buyer-maker signed aggressive-flow direction;
- outside-value acceptance as an explicit price-discovery invalidation;
- exact profile and execution evidence through NautilusTrader.

## Successor implication

The next hypothesis must not loosen BAVR to manufacture reversions.  It should
trade the complementary state indicated by the ablation: completed POC/value
migration with aligned aggressive inventory, followed by a retest which cannot
re-enter old value and a separate continuation response.  That successor is
AIMD.
