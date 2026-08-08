# Candidate 15 V25 — Session opening-range delivery diagnostic

**V25_SESSION_RANGE_DELIVERY_REJECTED_OR_UNDERPOWERED**

The screen uses three fixed liquidity handoffs, one attempt per session, a 15-minute opening balance, cross-market state, a completed five-minute breakout and the first one-minute retest. Stop and target are structural levels from the same session auction.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `21 / 0.057534246575342465`
- gross / net mean: `-3.321355922514628 / -23.32135592251463` bp
- win rate / payoff: `0.23809523809523808 / 2.037079090096009`
- net t-stat: `-0.8577460619873419`
- mean net structural R: `1.7527592283729203`
- positive months: `5 / 12`
- symbol counts: `{'ETHUSDT': 10, 'BTCUSDT': 2, 'SOLUSDT': 6, 'XRPUSDT': 3}`
- session counts: `{'LONDON': 10, 'ASIA': 5, 'NEW_YORK': 6}`
- exit reasons: `{'PRIOR_SESSION_OBJECTIVE': 5, 'STRUCTURAL_STOP': 16}`
- same-bar conservative stops: `0`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `19 / 0.052054794520547946`
- gross / net mean: `-13.96407986311255 / -33.96407986311255` bp
- win rate / payoff: `0.15789473684210525 / 1.6223827118092025`
- net t-stat: `-2.4733028978006875`
- mean net structural R: `1.6317950666826917`
- positive months: `2 / 11`
- symbol counts: `{'ETHUSDT': 5, 'SOLUSDT': 10, 'XRPUSDT': 4}`
- session counts: `{'ASIA': 9, 'LONDON': 8, 'NEW_YORK': 2}`
- exit reasons: `{'STRUCTURAL_STOP': 16, 'PRIOR_SESSION_OBJECTIVE': 2, 'SESSION_TIMEOUT': 1}`
- same-bar conservative stops: `0`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `1 / 0.03225806451612903`
- gross / net mean: `-19.927503112113776 / -39.927503112113776` bp
- win rate / payoff: `0.0 / None`
- net t-stat: `None`
- mean net structural R: `2.0511638129474057`
- positive months: `0 / 1`
- symbol counts: `{'BTCUSDT': 1}`
- session counts: `{'LONDON': 1}`
- exit reasons: `{'STRUCTURAL_STOP': 1}`
- same-bar conservative stops: `0`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- mean net structural R: `None`
- positive months: `0 / 0`
- symbol counts: `{}`
- session counts: `{}`
- exit reasons: `{}`
- same-bar conservative stops: `0`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `False`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_frequency: `False`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `False`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `False`
- symbol_concentration: `True`
- session_concentration: `True`

## Logic rejections
`{'OPENING_BODY_NOT_DIRECTIONAL': 5290, 'OPENING_FLOW_NOT_DIRECTIONAL': 556, 'INSUFFICIENT_NET_STRUCTURAL_R': 404, 'NO_ACCEPTED_BREAKOUT': 546, 'OBJECTIVE_CONSUMED_IN_OPENING': 1724, 'NO_FIRST_RETEST_HOLD': 363, 'CROSS_MARKET_BREADTH_UNRESOLVED': 210, 'INVALID_GEOMETRY': 44}`

## Decision
Do not tune the session family after these declared results; preserve only reusable geometry and move to another mechanism.

This is not synthetic account success. A passing result still requires frozen NautilusTrader orders, exact current-NAV 3% risk, one global slot and continuous-account validation.
