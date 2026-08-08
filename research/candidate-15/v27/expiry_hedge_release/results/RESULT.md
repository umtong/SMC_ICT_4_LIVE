# Candidate 15 V27 — Daily option-expiry hedge-release diagnostic

**V27_EXPIRY_HEDGE_RELEASE_REJECTED_OR_UNDERPOWERED**

The fixed 08:00 UTC context combines an abnormal four-hour focal/residual move, elevated futures OI and delivery-window pressure. A wholly subsequent 08:00-08:15 price/flow reversal starts the tradable leg. Binance OI is treated only as a positioning proxy, not option gamma.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- mean net structural R: `None`
- positive months: `0 / 0`
- symbol counts: `{}`
- exit reasons: `{}`
- same-bar conservative stops: `0`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- mean net structural R: `None`
- positive months: `0 / 0`
- symbol counts: `{}`
- exit reasons: `{}`
- same-bar conservative stops: `0`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- mean net structural R: `None`
- positive months: `0 / 0`
- symbol counts: `{}`
- exit reasons: `{}`
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
- symbol_concentration: `False`

## Logic rejections
`{'PRE_RETURN_NOT_EXTREME': 1190, 'RESIDUAL_NOT_EXTREME': 1018, 'PRE_VOLUME_WEAK': 1009, 'PRE_OI_BUILDUP_WEAK': 1195, 'DELIVERY_RETURN_NOT_ALIGNED': 553, 'DELIVERY_TAKER_NOT_ALIGNED': 641, 'PRE_TAKER_NOT_ALIGNED': 934, 'NO_POST_EXPIRY_RETURN_REVERSAL': 655, 'NO_POST_EXPIRY_FLOW_REVERSAL': 694, 'POST_EXPIRY_OLD_DIRECTION_OI_PERSISTS': 426, 'RESIDUAL_SHARE_TOO_SMALL': 479, 'INSUFFICIENT_NET_STRUCTURAL_R': 2}`

## Decision
Do not tune the expiry family after these declared results; move to another independent mechanism.

This is not a success or synthetic NAV claim. A pass still requires frozen NautilusTrader orders, exact current-NAV 3% risk sizing, all costs, one global slot and continuous-account validation.
