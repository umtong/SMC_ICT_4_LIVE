# Picasso RSI/BB/MACD precedence anatomy v64

- source periods: 5
- sampled calendar days: 2032
- raw candidate/path records: 156018
- conclusion: **public_picasso_edge_does_not_survive_causal_accounting**

| policy | raw candidates | trades | trades/day | mean R | median R | PF | ex-best R | daily diagnostic geom | max DD | same-bar trail |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| executed_precedence:source_reentry:freqtrade_bound_first | 71466 | 7781 | 3.829 | 0.030 | 0.065 | 1.38 | 0.030 | 0.333% | -82.64% | 6387 |
| executed_precedence:source_reentry:conservative_adverse_first | 71466 | 12029 | 5.920 | -0.023 | 0.017 | 0.73 | -0.023 | -0.430% | -99.99% | 0 |
| executed_precedence:continuous_signal_episode:freqtrade_bound_first | 71466 | 7781 | 3.829 | 0.030 | 0.065 | 1.38 | 0.030 | 0.333% | -82.64% | 6387 |
| executed_precedence:continuous_signal_episode:conservative_adverse_first | 71466 | 12029 | 5.920 | -0.023 | 0.017 | 0.73 | -0.023 | -0.430% | -99.99% | 0 |
| intended_parentheses:source_reentry:freqtrade_bound_first | 6543 | 3492 | 1.719 | 0.055 | 0.073 | 1.83 | 0.054 | 0.275% | -15.91% | 2943 |
| intended_parentheses:source_reentry:conservative_adverse_first | 6543 | 3761 | 1.851 | -0.029 | 0.012 | 0.71 | -0.029 | -0.167% | -96.80% | 0 |
| intended_parentheses:continuous_signal_episode:freqtrade_bound_first | 6543 | 3492 | 1.719 | 0.055 | 0.073 | 1.83 | 0.054 | 0.275% | -15.91% | 2943 |
| intended_parentheses:continuous_signal_episode:conservative_adverse_first | 6543 | 3761 | 1.851 | -0.029 | 0.012 | 0.71 | -0.029 | -0.167% | -96.80% | 0 |

## Predeclared assessment

- operator_precedence_materially_increases_candidates: `True`
- freqtrade_favorable_bound_order_materially_inflates_expectancy: `True`
- executed_precedence_survives_conservative_independent_accounting: `False`
- intended_logic_survives_conservative_independent_accounting: `False`
- independent_opportunity_density_reaches_one_per_day: `True`

## Next inference

Preserve only the variant and exit branches which remain positive under conservative intrabar ordering, one continuous signal episode and one global slot. Diagnose its remaining stop/exit loss states before any NautilusTrader promotion; do not tune the public ADX/RSI/BB/MACD parameters on these periods.

## Truth boundary

This is signal/path anatomy. It does not validate the public leverage report and is not a continuous NautilusTrader account.
