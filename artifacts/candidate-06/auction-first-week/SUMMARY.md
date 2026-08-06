# Candidate 06 v0.7 Rolling Auction Liquidity Relay

Selection uses fixed causal priority rather than maximum backtest return.

Selected: none

|variant|rc|gate|geom/day|trades|win rate|PF|max DD|largest win share|failures|
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
|auction_full|0|False|-0.501579%|10|30.00%|0.8029237253376571|15.76%|54.36%|geometric_daily_nav_growth, win_rate, positive_trade_count, profit_concentration|
|auction_srr_only|0|False|0.032222%|2|50.00%|1.0752644864807972|4.55%|100.00%|geometric_daily_nav_growth, trade_count, positive_trade_count, profit_concentration|
|auction_sac_only|0|False|0.551962%|9|33.33%|1.2461077434297485|15.44%|41.46%|geometric_daily_nav_growth, trade_count, win_rate, positive_trade_count, profit_concentration|
|auction_price_only|0|False|-1.353960%|12|25.00%|0.6563404195565595|19.20%|44.06%|geometric_daily_nav_growth, win_rate, positive_trade_count, profit_concentration|
