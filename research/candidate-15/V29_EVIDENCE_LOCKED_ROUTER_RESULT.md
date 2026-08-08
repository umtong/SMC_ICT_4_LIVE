# Candidate 15 V29 — Evidence-locked multi-family router

**V29_EVIDENCE_LOCKED_ROUTER_REJECTED_OR_UNDERPOWERED**

The unchanged V26 OI-buildup and V28 breadth-trend proposal streams are merged chronologically under one global position slot.

## Development
- interval: `2024-07-01 -> 2025-07-01`
- trades / day: `51 / 0.13972602739726028`
- gross / net mean: `76.1308801138505 / 56.130880113850566` bp
- win rate / payoff: `0.45098039215686275 / 1.8086059173857036`
- net t-stat: `0.8236137554276788`
- positive months: `6 / 12`
- family counts: `{'V28_BREADTH_TREND': 31, 'V26_OI_BUILDUP': 20}`
- symbol counts: `{'XRPUSDT': 15, 'BTCUSDT': 13, 'SOLUSDT': 13, 'ETHUSDT': 10}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- trades / day: `37 / 0.10136986301369863`
- gross / net mean: `91.12226740856353 / 71.1222674085636` bp
- win rate / payoff: `0.5135135135135135 / 1.840320048375624`
- net t-stat: `1.2765920139532978`
- positive months: `9 / 12`
- family counts: `{'V26_OI_BUILDUP': 19, 'V28_BREADTH_TREND': 18}`
- symbol counts: `{'BTCUSDT': 10, 'SOLUSDT': 10, 'ETHUSDT': 9, 'XRPUSDT': 8}`

## July 2026 confirmation
- interval: `2026-07-01 -> 2026-08-01`
- trades / day: `1 / 0.03225806451612903`
- gross / net mean: `17.275419545903 / -2.7245804540960004` bp
- win rate / payoff: `0.0 / None`
- net t-stat: `None`
- positive months: `0 / 1`
- family counts: `{'V26_OI_BUILDUP': 1}`
- symbol counts: `{'SOLUSDT': 1}`

## Latest August 1-7 pulse
- interval: `2026-08-01 -> 2026-08-08`
- trades / day: `0 / 0.0`
- gross / net mean: `None / None` bp
- win rate / payoff: `None / None`
- net t-stat: `None`
- positive months: `0 / 0`
- family counts: `{}`
- symbol counts: `{}`

## Advance checks
- positive_development_mean_net: `True`
- positive_stability_mean_net: `True`
- stability_net_t_stat: `True`
- stability_positive_month_share: `True`
- stability_frequency: `False`
- positive_july_confirmation_mean_net: `False`
- july_confirmation_trade_count: `False`
- positive_latest_pulse_mean_net: `False`
- latest_pulse_trade_count: `False`
- family_concentration: `True`
- symbol_concentration: `True`

## Decision
The two positive historical families do not jointly satisfy recent confirmation and frequency. Preserve them only as rare components.

This integration does not synthesize account NAV. A pass still requires one frozen NautilusTrader continuous account with exact current-NAV 3% risk sizing and actual execution costs.
