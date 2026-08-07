# Candidate 06 v3.0 Cross-Venue Price-Discovery Bifurcation

Terminal status: `FIRST_WEEK_LOGIC_GATE_FAILED`
Selected: none

|variant|week|eligible|gate|geom/day|trades|wins|win rate|PF|max DD|failures|
|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|
|cvpd_full_bifurcation|1|True|False|-0.434170%|1|0|0.00%|0.0|3.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|cvpd_perpetual_false_break_only|1|True|False|0.000000%|0|0|0.00%|None|0.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|cvpd_spot_led_relay_only|1|True|False|-0.434170%|1|0|0.00%|0.0|3.00%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|cvpd_price_divergence_without_basis_ablation|1|False|False|-0.866527%|2|0|0.00%|0.0|5.91%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|

## Diagnoses

- **cvpd_full_bifurcation**: `NEGATIVE_COST_AFTER_EXPECTANCY` — {'classification': 'NEGATIVE_COST_AFTER_EXPECTANCY', 'geometric_daily_nav_growth': -0.004341695384694755, 'trades': 1, 'wins': 0, 'win_rate': 0.0, 'profit_factor': 0.0, 'max_drawdown_nav': 0.029998863075400003, 'largest_positive_trade_share': 1.0, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'entry_abstentions': {'DELAYED_PRICE_OUTSIDE_SHORT_BRACKET': 1, 'FAVORABLE_MOVE_ALREADY_CONSUMED': 2, 'NET_REWARD_RISK_ERODED_AFTER_DELAY': 6}, 'cross_venue_context': {'exact_timestamp_match': True, 'perpetual_provider': 'Binance public USDT-M futures daily klines', 'perpetual_rows': 10080, 'spot_provider': 'Binance public spot daily klines', 'spot_rows': 10080}}
- **cvpd_perpetual_false_break_only**: `NO_COMPLETED_CROSS_VENUE_RESPONSE` — {'classification': 'NO_COMPLETED_CROSS_VENUE_RESPONSE', 'geometric_daily_nav_growth': 0.0, 'trades': 0, 'wins': 0, 'win_rate': 0.0, 'profit_factor': None, 'max_drawdown_nav': 0.0, 'largest_positive_trade_share': 1.0, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'entry_abstentions': {'DELAYED_PRICE_OUTSIDE_SHORT_BRACKET': 1, 'FAVORABLE_MOVE_ALREADY_CONSUMED': 1, 'NET_REWARD_RISK_ERODED_AFTER_DELAY': 2}, 'cross_venue_context': {'exact_timestamp_match': True, 'perpetual_provider': 'Binance public USDT-M futures daily klines', 'perpetual_rows': 10080, 'spot_provider': 'Binance public spot daily klines', 'spot_rows': 10080}}
- **cvpd_spot_led_relay_only**: `NEGATIVE_COST_AFTER_EXPECTANCY` — {'classification': 'NEGATIVE_COST_AFTER_EXPECTANCY', 'geometric_daily_nav_growth': -0.004341695384694755, 'trades': 1, 'wins': 0, 'win_rate': 0.0, 'profit_factor': 0.0, 'max_drawdown_nav': 0.029998863075400003, 'largest_positive_trade_share': 1.0, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'entry_abstentions': {'FAVORABLE_MOVE_ALREADY_CONSUMED': 1, 'NET_REWARD_RISK_ERODED_AFTER_DELAY': 4}, 'cross_venue_context': {'exact_timestamp_match': True, 'perpetual_provider': 'Binance public USDT-M futures daily klines', 'perpetual_rows': 10080, 'spot_provider': 'Binance public spot daily klines', 'spot_rows': 10080}}
- **cvpd_price_divergence_without_basis_ablation**: `NEGATIVE_COST_AFTER_EXPECTANCY` — {'classification': 'NEGATIVE_COST_AFTER_EXPECTANCY', 'geometric_daily_nav_growth': -0.008665271581613898, 'trades': 2, 'wins': 0, 'win_rate': 0.0, 'profit_factor': 0.0, 'max_drawdown_nav': 0.059102651902299984, 'largest_positive_trade_share': 1.0, 'gate_failures': ['geometric_daily_nav_growth', 'trade_count', 'win_rate', 'positive_trade_count', 'profit_concentration'], 'entry_abstentions': {'DELAYED_PRICE_OUTSIDE_SHORT_BRACKET': 2, 'FAVORABLE_MOVE_ALREADY_CONSUMED': 4, 'NET_REWARD_RISK_ERODED_AFTER_DELAY': 13}, 'cross_venue_context': {'exact_timestamp_match': True, 'perpetual_provider': 'Binance public USDT-M futures daily klines', 'perpetual_rows': 10080, 'spot_provider': 'Binance public spot daily klines', 'spot_rows': 10080}}

## Fixed causal contract

- Spot and perpetual bars must share the exact completed one-minute timestamp.
- Basis and activity baselines exclude the current decision bar.
- The initiating divergence bar cannot emit an entry.
- A separate perpetual response is required.
- The perpetual is the only traded instrument; one native Nautilus account and one global slot are used.
- Risk remains three percent of whole-account NAV per approved trade.
