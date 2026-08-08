# Candidate 15 V18 — Spot/perpetual leadership state diagnostic

**V18_SPOT_PERP_ROUTER_REJECTED_OR_UNDERPOWERED**

## Development
- interval: `2024-07-01 -> 2025-07-01`
- selected trades / day: `161 / 0.4410958904109589`
- gross / net mean: `4.782922219595452 / -11.217077780404548` bp
- win rate / payoff: `0.453416149068323 / 0.9996493062444143`
- net t-stat: `-0.6513384841706282`
- positive months: `6 / 12`
- route stats: `{'PERP_LED_DELEVERAGING_NONCONFIRMATION_REVERSAL': {'trades': 77, 'mean_net_bps': -10.689780865034198, 'win_rate': 0.5064935064935064, 'net_t_stat': -0.9146027084390033}, 'SPOT_LED_POSITION_BUILDUP_CONTINUATION': {'trades': 84, 'mean_net_bps': -11.700433286160708, 'win_rate': 0.40476190476190477, 'net_t_stat': -0.3735394142176104}}`
- symbol counts: `{'BTCUSDT': 52, 'SOLUSDT': 39, 'ETHUSDT': 43, 'XRPUSDT': 27}`

## Year-long stability
- interval: `2025-07-01 -> 2026-07-01`
- selected trades / day: `182 / 0.4986301369863014`
- gross / net mean: `19.321677581603208 / 3.3216775816032067` bp
- win rate / payoff: `0.46153846153846156 / 1.2750984304741533`
- net t-stat: `0.40082583719531223`
- positive months: `7 / 12`
- route stats: `{'PERP_LED_DELEVERAGING_NONCONFIRMATION_REVERSAL': {'trades': 121, 'mean_net_bps': -5.937565045557103, 'win_rate': 0.4297520661157025, 'net_t_stat': -0.7731225019686949}, 'SPOT_LED_POSITION_BUILDUP_CONTINUATION': {'trades': 61, 'mean_net_bps': 21.688371973183493, 'win_rate': 0.5245901639344263, 'net_t_stat': 1.1189860266766227}}`
- symbol counts: `{'ETHUSDT': 58, 'BTCUSDT': 57, 'XRPUSDT': 28, 'SOLUSDT': 39}`

## Latest July 2026 holdout
- interval: `2026-07-01 -> 2026-08-01`
- selected trades / day: `7 / 0.22580645161290322`
- gross / net mean: `-4.550139967587828 / -20.550139967587832` bp
- win rate / payoff: `0.14285714285714285 / 0.3794303505536594`
- net t-stat: `-3.011242216104397`
- positive months: `0 / 1`
- route stats: `{'PERP_LED_DELEVERAGING_NONCONFIRMATION_REVERSAL': {'trades': 3, 'mean_net_bps': -19.50326600487986, 'win_rate': 0.0, 'net_t_stat': -3.5206595458469914}, 'SPOT_LED_POSITION_BUILDUP_CONTINUATION': {'trades': 4, 'mean_net_bps': -21.3352954396188, 'win_rate': 0.25, 'net_t_stat': -1.7586077111304683}}`
- symbol counts: `{'BTCUSDT': 5, 'ETHUSDT': 1, 'SOLUSDT': 1}`

## Advance checks
- positive_development_mean_net: `False`
- positive_stability_mean_net: `True`
- stability_net_t_stat: `False`
- stability_positive_month_share: `False`
- stability_independent_frequency: `False`
- positive_latest_holdout_mean_net: `False`
- latest_holdout_trade_count: `False`
- symbol_concentration: `True`

## Cross-split route evidence
`{'PERP_LED_DELEVERAGING_NONCONFIRMATION_REVERSAL': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 77, 'mean_net_bps': -10.689780865034198, 'win_rate': 0.5064935064935064, 'net_t_stat': -0.9146027084390033}, 'stability': {'trades': 121, 'mean_net_bps': -5.937565045557103, 'win_rate': 0.4297520661157025, 'net_t_stat': -0.7731225019686949}, 'latest_holdout': {'trades': 3, 'mean_net_bps': -19.50326600487986, 'win_rate': 0.0, 'net_t_stat': -3.5206595458469914}}}, 'SPOT_LED_POSITION_BUILDUP_CONTINUATION': {'positive_across_all_declared_splits': False, 'splits': {'development': {'trades': 84, 'mean_net_bps': -11.700433286160708, 'win_rate': 0.40476190476190477, 'net_t_stat': -0.3735394142176104}, 'stability': {'trades': 61, 'mean_net_bps': 21.688371973183493, 'win_rate': 0.5245901639344263, 'net_t_stat': 1.1189860266766227}, 'latest_holdout': {'trades': 4, 'mean_net_bps': -21.3352954396188, 'win_rate': 0.25, 'net_t_stat': -1.7586077111304683}}}}`

## Decision
The integrated spot/perpetual router failed at least one predeclared gate. Cross-split-positive routes: []. Do not tune numeric thresholds; retain only those routes and move to another independent mechanism.

This remains a mechanism screen. It neither synthesizes NAV nor replaces the required frozen NautilusTrader continuous-account validation.
