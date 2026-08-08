# Candidate 11 growth decomposition

**BOTH_EVENT_RATE_AND_EVENT_QUALITY_FAILED**

Holdout activity fell to less than half the development event rate, but the larger failure was event quality: average log growth per closed trade changed from positive to negative. No-trade dilution alone cannot explain the sign reversal.

## Development versus untouched holdout

- event rate/day: `0.314286` -> `0.142857`
- average log growth/trade: `0.037423` -> `-0.005439`
- equivalent average return/trade: `3.813203%` -> `-0.542426%`
- daily geometric growth: `1.183094%` -> `-0.077670%`
- event-rate ratio: `0.454545`
- Shapley share of absolute log-growth gap from frequency: `21.864493%`
- Shapley share of absolute log-growth gap from event quality: `78.135507%`

The decomposition is descriptive and uses the recorded NautilusTrader account-NAV multiples.
