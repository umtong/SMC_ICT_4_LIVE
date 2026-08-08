# Candidate 39 external idea mining

Research date: 2026-08-09.  Purpose: obtain machine-testable, non-scalping intraday decisions before looking for another local threshold variation.  Source reputation and claimed performance were not used as gates; every item below was treated only as an idea lead.

## Adopted mechanisms

### 1. Spot/perpetual disagreement is a state diagnostic, not an entry by itself

Reviewed:

- The Kingfisher Telegram public channel: <https://t.me/s/thekingfisher_btc>
- Specific public examples of futures CVD building against weak spot CVD and of spot-led demand: <https://t.me/s/thekingfisher_btc/4036>, <https://t.me/s/thekingfisher_btc/4056>, <https://t.me/s/thekingfisher_btc/4063>
- SEALCRYPTO ONCHAIN public Telegram feed: <https://t.me/s/sealcryptocvd>

Extracted decision:

- Do not infer direction from open interest alone.
- Require price to leave a completed range and require signed aggressor flow to agree before treating rising OI as position building.
- Treat leverage-led movement without spot/peer confirmation as vulnerable rather than automatically bullish or bearish.

Machine adaptation:

- `BUILD_ACCEPT_CONTINUATION`: prior-range break + positive OI change + aligned opening/tail flow + boundary acceptance.
- `PEER_LED_REPRICING`: BTC/breadth leads, a lagging asset accepts its own boundary, and OI/flow agree.
- Missing OI or contradictory flow resolves to `UNRESOLVED`.

### 2. OI changes and liquidation prints are event clocks, not standalone signals

Reviewed:

- Open Interest Tracker: <https://t.me/s/OpenInterestTracker>
- Open Interest Variation alerts: <https://t.me/s/OpenInterest_Alert>
- Raw liquidation print feed: <https://t.me/s/liquidationcrypto>
- Kingfisher liquidation-map examples: <https://t.me/s/thekingfisher_btc/4036>

Extracted decision:

- Rising OI means positions were added, not that longs or shorts necessarily dominate.
- Falling OI during an excursion can mark forced position reduction, but reversal is not valid until price reclaims the boundary and later opposite initiative appears.
- Heatmap zones may define where an interaction/objective could exist.  They are never a direct entry or a claim that price must be attracted to a level.

Machine adaptation:

- `CASCADE_RECLAIM_REVERSAL`: boundary sweep + OI contraction + first response reclaim + frozen flow flip + distinct later opposite initiative.
- The first reclaim cannot simultaneously serve as both state evidence and entry trigger.
- Natural target is the opposite edge of the completed pre-event range, subject to causal target-space geometry.

### 3. Community breakout observations become acceptance and target-space rules

Reviewed Reddit:

- ETH order-flow discussion combining compression, CVD and OI: <https://www.reddit.com/r/ethtrader/comments/1s0yxwr/orderflow_snapshot_of_ethereum_22032026/>

Reviewed DCInside:

- A chart-community post arguing that a meaningful line broken with one-way volume should continue with only a shallow correction: <https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=3275080>
- A breakdown/retest example used as scenario invalidation context: <https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=4586965>
- A curated chart-study index was inspected, but its scalping material was explicitly excluded: <https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=3564918>

Extracted decision:

- CVD/OI pressure inside compression is only latent energy.  Entry waits for a completed price break and acceptance/retest.
- A true continuation should not immediately consume all target space or make a deep response back through the boundary.
- A failed break is not automatically a reversal; it needs a separate state transition.

Machine adaptation:

- Evaluate a completed 15-minute auction and three completed one-minute response bars.
- Require response close/geometry to preserve a structural objective.
- Reject excessive extension, deep failure, ambiguous cross-asset direction, or insufficient reward space.

## Korean-community search coverage and limits

The following were searched directly in Korean with combinations of `비트코인`, `선물`, `돌파`, `리테스트`, `거래량`, `미결제약정`, `OI`, and `CVD`:

- DCInside Bitcoin/chart communities: useful text-indexed posts were available; the acceptance and shallow-response ideas above were retained.
- FMKorea coin-related pages: public search results were mostly unrelated, image-only, promotional, or not text-indexed.  No direct rule was adopted.
- Coinpan and Cobak: the public search session returned no sufficiently specific indexed discussion to justify a machine rule.  No direct rule was adopted.

This is not a judgment about those communities.  It records what could actually be inspected and converted into a falsifiable decision in this run.

## Explicitly rejected material

- Signal-room win-rate and profit screenshots.
- Entries whose entire logic is “liquidation cluster = magnet”.
- One- to five-minute scalping recipes and outcome-selected chart examples.
- OI-up = bullish and OI-down = bearish shortcuts.
- CVD divergence without a completed price state transition.
- Adding filters merely because a previous backtest lost.

## Resulting hypothesis

A non-scalping intraday edge may exist when a completed range interaction is routed into one of three mutually interpretable states:

1. new position build accepted beyond a boundary;
2. forced-position cascade exhausted and then reversed by a later initiative;
3. leader-established cross-asset repricing with unused space in a laggard.

The hypothesis is implemented in `router.py`; only NautilusTrader account results determine whether it survives.
