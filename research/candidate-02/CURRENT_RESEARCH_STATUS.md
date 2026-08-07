# Candidate-02 exact research status

- Updated UTC: `2026-08-06T10:38:10.204283+00:00`
- Performance engine: **NautilusTrader 1.230.0 only**
- Risk per trade: **3% of current account NAV**
- Custom backtest engine: **not permitted and not accepted as evidence**
- Project success: **false**
- Current terminal classification: **NO_COMPLETE_CANDIDATE**

## Sequential gate results

| Gate | Status | Trades/day | Win rate | PF after cost | Geometric growth/day | MDD |
|---|---|---:|---:|---:|---:|---:|
| v77_first_week_decision | PROMOTED_TO_LOCKED_SECOND_WEEK | — | — | — | — | — |
| v77_week2_decision | REJECTED_LOCKED_SECOND_WEEK | 0.714 | 40.000% | 0.232 | -1.022% | -9.047% |

## Exact machine records

### `v77_first_week_decision.json`

```json
{
  "candidate_family": "candidate-02-v77-quarter-hour-metaorder-wave-reward-duration-plateau",
  "custom_backtest_engine": false,
  "evidence_commit": "a0e65980335c993184c4714ef11b6775c2cfd2cc",
  "evidence_run_id": 31082478739,
  "excluded_week_count": 64,
  "passing_points": [
    [
      1.25,
      120
    ],
    [
      1.25,
      180
    ],
    [
      1.25,
      240
    ]
  ],
  "performance_engine": "NautilusTrader 1.230.0",
  "plateau_points": [
    [
      1.25,
      120
    ],
    [
      1.25,
      180
    ],
    [
      1.25,
      240
    ]
  ],
  "plateau_rule": {
    "adjacency": "same target and adjacent hold, or same hold and adjacent 1.00/1.25 target",
    "central_preference": {
      "maximum_holding_minutes": 180,
      "target_range_multiple": 1.25
    },
    "minimum_passing_adjacent_points": 2
  },
  "risk_fraction": 0.03,
  "rows": [
    {
      "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_120",
      "checks": {
        "drawdown": true,
        "engine": true,
        "growth": true,
        "no_custom_engine": true,
        "profit_factor": true,
        "risk": true,
        "trades_per_day": true,
        "win_rate": true
      },
      "config_path": "research/candidate-02/v77_60m_1p25_120_config.json",
      "maximum_holding_minutes": 120,
      "metrics": {
        "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_120",
        "custom_backtest_engine": false,
        "data_integrity": {
          "columns_path": "inputs/v77-first-week/candidate-02-v48-first-week/columns.json",
          "feature_first_open_utc": "2024-09-14T00:01:00+00:00",
          "feature_last_open_utc": "2024-09-24T00:00:00+00:00",
          "feature_rows": 14400,
          "forward_feature_columns_used": false,
          "npz_path": "inputs/v77-first-week/candidate-02-v48-first-week/v48_features.npz",
          "raw_directory": "inputs/v77-first-week/.cache/candidate-02/v77-first-week/binance_1m",
          "raw_first_close_utc": "2024-09-14T00:01:00+00:00",
          "raw_last_close_utc": "2024-09-23T01:00:00+00:00",
          "raw_rows": 13020
        },
        "decision": "ADVANCE_TO_SECOND_WEEK",
        "effective_commissions_usdt": 5939.61959622,
        "engine": "NautilusTrader 1.230.0",
        "engine_result": {
          "iterations": 13020,
          "stats_general": {},
          "stats_pnls": {
            "USDT": {
              "Avg Loser": -1985.302294895,
              "Avg Winner": 1929.3877133671429,
              "Expectancy": 1059.45660042,
              "Max Loser": -3209.6147269499997,
              "Max Winner": 2506.14441878,
              "Min Loser": -760.98986284,
              "Min Winner": 1091.98452648,
              "PnL (total)": 9535.10940378,
              "PnL% (total)": 9.535109403780007,
              "Win Rate": 0.7777777777777778
            }
          },
          "stats_returns": {
            "Average (Return)": 0.011675708699851392,
            "Average Loss (Return)": -0.026852828723877065,
            "Average Win (Return)": 0.02405169966453764,
            "Profit Factor": 4.478429425789189,
            "Returns Volatility (252 days)": 0.36274257304292185,
            "Risk Return Ratio": 0.5109577517925065,
            "Sharpe Ratio (252 days)": 8.111202850221838,
            "Sortino Ratio (252 days)": 19.522636733063937
          },
          "summary": {
            "account.BINANCE.balance.USDT.free": "109535.10940378 USDT",
            "account.BINANCE.balance.USDT.locked": "0.00000000 USDT",
            "account.BINANCE.balance.USDT.total": "109535.10940378 USDT",
            "account.BINANCE.base_currency": "USDT",
            "account.BINANCE.event_count": "48",
            "account.BINANCE.id": "BINANCE-001",
            "account.BINANCE.type": "MARGIN",
            "iterations": "13020",
            "orders.closed": "29",
            "orders.emulated": "0",
            "orders.inflight": "0",
            "orders.open": "0",
            "orders.total": "29",
            "positions.closed": "1",
            "positions.open": "0",
            "positions.snapshots": "8",
            "positions.total": "1",
            "positions.total_with_snapshots": "9",
            "total_events": "76",
            "venues.total": "1"
          },
          "total_events": 76,
          "total_orders": 29,
          "total_positions": 9
        },
        "evaluation_days": 7.0,
        "evaluation_end_utc": "2024-09-23T00:00:00+00:00",
        "evaluation_start_utc": "2024-09-16T00:00:00+00:00",
        "execution_model": {
          "account_source_of_truth": true,
          "bar_adaptive_high_low_ordering": true,
          "bar_execution": true,
          "bracket_orders": true,
          "effective_maker_commission_rate": "0.00030",
          "effective_taker_commission_rate": "0.00085",
          "liquidation_enabled": false,
          "liquidation_requested": true
        },
        "fills": 18,
        "final_nav_usdt": 109535.10940378,
        "flat_at_end": true,
        "geometric_daily_growth_after_cost": 0.01309571404008203,
        "gross_loss_after_cost_usdt": -3970.6045897899894,
        "gross_profit_after_cost_usdt": 13505.713993569996,
        "losses": 2,
        "maximum_effective_notional_multiple": 8.62754764242002,
        "maximum_mark_to_market_drawdown": -0.048896907205825846,
        "maximum_planned_loss_to_budget": 0.9999601409985861,
        "nav_factor": 1.0953510940378002,
        "orders": 29,
        "pass_checks": {
          "flat_at_end": true,
          "maximum_drawdown": true,
          "minimum_geometric_daily_growth": true,
          "minimum_profit_factor": true,
          "minimum_trades_per_day": true,
          "minimum_win_rate": true,
          "planned_loss_budget": true
        },
        "positions_rows": 9,
        "profit_factor_after_cost": 3.4014250696980963,
        "risk_fraction": 0.03,
        "runtime_diagnostics": {
          "entry_pending": false,
          "position_flat": true,
          "runtime": {
            "ENTRY_BRACKET_SUBMITTED": 9,
            "FORCED_EXIT_MAX_HOLD": 2,
            "POSITION_CLOSED": 9,
            "POSITION_OPENED": 9
          },
          "scheduled_signals": 9,
          "selected_signal": null
        },
        "scheduled_signals": 9,
        "stage": "first_random_btc_week_nautilustrader",
        "starting_nav_usdt": 100000.0,
        "submitted_signals": 9,
        "target_met": true,
        "trades": 9,
        "trades_per_day": 1.2857142857142858,
        "win_rate": 0.7777777777777778,
        "wins": 7
      },
      "metrics_path": "inputs/v77-first-week/artifacts/candidate-02-v77-60m_1p25_120/metrics.json",
      "passes_all": true,
      "target_range_multiple": 1.25
    },
    {
      "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_180",
      "checks": {
        "drawdown": true,
        "engine": true,
        "growth": true,
        "no_custom_engine": true,
        "profit_factor": true,
        "risk": true,
        "trades_per_day": true,
        "win_rate": true
      },
      "config_path": "research/candidate-02/v77_60m_1p25_180_config.json",
      "maximum_holding_minutes": 180,
      "metrics": {
        "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_180",
        "custom_backtest_engine": false,
        "data_integrity": {
          "columns_path": "inputs/v77-first-week/candidate-02-v48-first-week/columns.json",
          "feature_first_open_utc": "2024-09-14T00:01:00+00:00",
          "feature_last_open_utc": "2024-09-24T00:00:00+00:00",
          "feature_rows": 14400,
          "forward_feature_columns_used": false,
          "npz_path": "inputs/v77-first-week/candidate-02-v48-first-week/v48_features.npz",
          "raw_directory": "inputs/v77-first-week/.cache/candidate-02/v77-first-week/binance_1m",
          "raw_first_close_utc": "2024-09-14T00:01:00+00:00",
          "raw_last_close_utc": "2024-09-23T01:00:00+00:00",
          "raw_rows": 13020
        },
        "decision": "ADVANCE_TO_SECOND_WEEK",
        "effective_commissions_usdt": 5904.92671868,
        "engine": "NautilusTrader 1.230.0",
        "engine_result": {
          "iterations": 13020,
          "stats_general": {},
          "stats_pnls": {
            "USDT": {
              "Avg Loser": -2048.50222679,
              "Avg Winner": 1810.306847842857,
              "Expectancy": 952.7937201466666,
              "Max Loser": -3186.31981933,
              "Max Winner": 2484.24099117,
              "Min Loser": -910.6846342499999,
              "Min Winner": 923.77101656,
              "PnL (total)": 8575.143481320001,
              "PnL% (total)": 8.575143481320003,
              "Win Rate": 0.7777777777777778
            }
          },
          "stats_returns": {
            "Average (Return)": 0.010566825376298175,
            "Average Loss (Return)": -0.026853971642113117,
            "Average Win (Return)": 0.022277714930499702,
            "Profit Factor": 4.1479367051172416,
            "Returns Volatility (252 days)": 0.36566902537649354,
            "Risk Return Ratio": 0.4587294545554692,
            "Sharpe Ratio (252 days)": 7.282104334884464,
            "Sortino Ratio (252 days)": 17.667750938698124
          },
          "summary": {
            "account.BINANCE.balance.USDT.free": "108575.14348132 USDT",
            "account.BINANCE.balance.USDT.locked": "0.00000000 USDT",
            "account.BINANCE.balance.USDT.total": "108575.14348132 USDT",
            "account.BINANCE.base_currency": "USDT",
            "account.BINANCE.event_count": "48",
            "account.BINANCE.id": "BINANCE-001",
            "account.BINANCE.type": "MARGIN",
            "iterations": "13020",
            "orders.closed": "29",
            "orders.emulated": "0",
            "orders.inflight": "0",
            "orders.open": "0",
            "orders.total": "29",
            "positions.closed": "1",
            "positions.open": "0",
            "positions.snapshots": "8",
            "positions.total": "1",
            "positions.total_with_snapshots": "9",
            "total_events": "76",
            "venues.total": "1"
          },
          "total_events": 76,
          "total_orders": 29,
          "total_positions": 9
        },
        "evaluation_days": 7.0,
        "evaluation_end_utc": "2024-09-23T00:00:00+00:00",
        "evaluation_start_utc": "2024-09-16T00:00:00+00:00",
        "execution_model": {
          "account_source_of_truth": true,
          "bar_adaptive_high_low_ordering": true,
          "bar_execution": true,
          "bracket_orders": true,
          "effective_maker_commission_rate": "0.00030",
          "effective_taker_commission_rate": "0.00085",
          "liquidation_enabled": false,
          "liquidation_requested": true
        },
        "fills": 18,
        "final_nav_usdt": 108575.14348132,
        "flat_at_end": true,
        "geometric_daily_growth_after_cost": 0.01182252780390769,
        "gross_loss_after_cost_usdt": -4097.0044535799825,
        "gross_profit_after_cost_usdt": 12672.147934899986,
        "losses": 2,
        "maximum_effective_notional_multiple": 8.627418906735844,
        "maximum_mark_to_market_drawdown": -0.05584250523737544,
        "maximum_planned_loss_to_budget": 0.9999882060228072,
        "nav_factor": 1.0857514348132,
        "orders": 29,
        "pass_checks": {
          "flat_at_end": true,
          "maximum_drawdown": true,
          "minimum_geometric_daily_growth": true,
          "minimum_profit_factor": true,
          "minimum_trades_per_day": true,
          "minimum_win_rate": true,
          "planned_loss_budget": true
        },
        "positions_rows": 9,
        "profit_factor_after_cost": 3.093027620174296,
        "risk_fraction": 0.03,
        "runtime_diagnostics": {
          "entry_pending": false,
          "position_flat": true,
          "runtime": {
            "ENTRY_BRACKET_SUBMITTED": 9,
            "FORCED_EXIT_MAX_HOLD": 2,
            "POSITION_CLOSED": 9,
            "POSITION_OPENED": 9
          },
          "scheduled_signals": 9,
          "selected_signal": null
        },
        "scheduled_signals": 9,
        "stage": "first_random_btc_week_nautilustrader",
        "starting_nav_usdt": 100000.0,
        "submitted_signals": 9,
        "target_met": true,
        "trades": 9,
        "trades_per_day": 1.2857142857142858,
        "win_rate": 0.7777777777777778,
        "wins": 7
      },
      "metrics_path": "inputs/v77-first-week/artifacts/candidate-02-v77-60m_1p25_180/metrics.json",
      "passes_all": true,
      "target_range_multiple": 1.25
    },
    {
      "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_240",
      "checks": {
        "drawdown": true,
        "engine": true,
        "growth": true,
        "no_custom_engine": true,
        "profit_factor": true,
        "risk": true,
        "trades_per_day": true,
        "win_rate": true
      },
      "config_path": "research/candidate-02/v77_60m_1p25_240_config.json",
      "maximum_holding_minutes": 240,
      "metrics": {
        "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_240",
        "custom_backtest_engine": false,
        "data_integrity": {
          "columns_path": "inputs/v77-first-week/candidate-02-v48-first-week/columns.json",
          "feature_first_open_utc": "2024-09-14T00:01:00+00:00",
          "feature_last_open_utc": "2024-09-24T00:00:00+00:00",
          "feature_rows": 14400,
          "forward_feature_columns_used": false,
          "npz_path": "inputs/v77-first-week/candidate-02-v48-first-week/v48_features.npz",
          "raw_directory": "inputs/v77-first-week/.cache/candidate-02/v77-first-week/binance_1m",
          "raw_first_close_utc": "2024-09-14T00:01:00+00:00",
          "raw_last_close_utc": "2024-09-23T01:00:00+00:00",
          "raw_rows": 13020
        },
        "decision": "ADVANCE_TO_SECOND_WEEK",
        "effective_commissions_usdt": 5920.76269827,
        "engine": "NautilusTrader 1.230.0",
        "engine_result": {
          "iterations": 13020,
          "stats_general": {},
          "stats_pnls": {
            "USDT": {
              "Avg Loser": -1878.47512205,
              "Avg Winner": 1849.772906547143,
              "Expectancy": 1021.2733446366669,
              "Max Loser": -3193.5200635,
              "Max Winner": 2498.29841486,
              "Min Loser": -563.4301806,
              "Min Winner": 1088.5876438799999,
              "PnL (total)": 9191.46010173,
              "PnL% (total)": 9.191460101730001,
              "Win Rate": 0.7777777777777778
            }
          },
          "stats_returns": {
            "Average (Return)": 0.011277588090952806,
            "Average Loss (Return)": -0.026851644530569763,
            "Average Win (Return)": 0.02341446985163844,
            "Profit Factor": 4.3599694284984665,
            "Returns Volatility (252 days)": 0.3620853721082357,
            "Risk Return Ratio": 0.49443080183364524,
            "Sharpe Ratio (252 days)": 7.848845653092503,
            "Sortino Ratio (252 days)": 18.85778164721386
          },
          "summary": {
            "account.BINANCE.balance.USDT.free": "109191.46010173 USDT",
            "account.BINANCE.balance.USDT.locked": "0.00000000 USDT",
            "account.BINANCE.balance.USDT.total": "109191.46010173 USDT",
            "account.BINANCE.base_currency": "USDT",
            "account.BINANCE.event_count": "49",
            "account.BINANCE.id": "BINANCE-001",
            "account.BINANCE.type": "MARGIN",
            "iterations": "13020",
            "orders.closed": "29",
            "orders.emulated": "0",
            "orders.inflight": "0",
            "orders.open": "0",
            "orders.total": "29",
            "positions.closed": "1",
            "positions.open": "0",
            "positions.snapshots": "8",
            "positions.total": "1",
            "positions.total_with_snapshots": "9",
            "total_events": "77",
            "venues.total": "1"
          },
          "total_events": 77,
          "total_orders": 29,
          "total_positions": 9
        },
        "evaluation_days": 7.0,
        "evaluation_end_utc": "2024-09-23T00:00:00+00:00",
        "evaluation_start_utc": "2024-09-16T00:00:00+00:00",
        "execution_model": {
          "account_source_of_truth": true,
          "bar_adaptive_high_low_ordering": true,
          "bar_execution": true,
          "bracket_orders": true,
          "effective_maker_commission_rate": "0.00030",
          "effective_taker_commission_rate": "0.00085",
          "liquidation_enabled": false,
          "liquidation_requested": true
        },
        "fills": 19,
        "final_nav_usdt": 109191.46010173,
        "flat_at_end": true,
        "geometric_daily_growth_after_cost": 0.012641040958865979,
        "gross_loss_after_cost_usdt": -3756.9502440999786,
        "gross_profit_after_cost_usdt": 12948.41034582998,
        "losses": 2,
        "maximum_effective_notional_multiple": 8.627781601856483,
        "maximum_mark_to_market_drawdown": -0.053624457764208544,
        "maximum_planned_loss_to_budget": 0.9999783819687179,
        "nav_factor": 1.0919146010173,
        "orders": 29,
        "pass_checks": {
          "flat_at_end": true,
          "maximum_drawdown": true,
          "minimum_geometric_daily_growth": true,
          "minimum_profit_factor": true,
          "minimum_trades_per_day": true,
          "minimum_win_rate": true,
          "planned_loss_budget": true
        },
        "positions_rows": 9,
        "profit_factor_after_cost": 3.4465216477552585,
        "risk_fraction": 0.03,
        "runtime_diagnostics": {
          "entry_pending": false,
          "position_flat": true,
          "runtime": {
            "ENTRY_BRACKET_SUBMITTED": 9,
            "FORCED_EXIT_MAX_HOLD": 2,
            "POSITION_CLOSED": 9,
            "POSITION_OPENED": 9
          },
          "scheduled_signals": 9,
          "selected_signal": null
        },
        "scheduled_signals": 9,
        "stage": "first_random_btc_week_nautilustrader",
        "starting_nav_usdt": 100000.0,
        "submitted_signals": 9,
        "target_met": true,
        "trades": 9,
        "trades_per_day": 1.2857142857142858,
        "win_rate": 0.7777777777777778,
        "wins": 7
      },
      "metrics_path": "inputs/v77-first-week/artifacts/candidate-02-v77-60m_1p25_240/metrics.json",
      "passes_all": true,
      "target_range_multiple": 1.25
    },
    {
      "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1x_180",
      "checks": {
        "drawdown": true,
        "engine": true,
        "growth": false,
        "no_custom_engine": true,
        "profit_factor": true,
        "risk": true,
        "trades_per_day": true,
        "win_rate": true
      },
      "config_path": "research/candidate-02/v77_60m_1x_180_config.json",
      "maximum_holding_minutes": 180,
      "metrics": {
        "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1x_180",
        "custom_backtest_engine": false,
        "data_integrity": {
          "columns_path": "inputs/v77-first-week/candidate-02-v48-first-week/columns.json",
          "feature_first_open_utc": "2024-09-14T00:01:00+00:00",
          "feature_last_open_utc": "2024-09-24T00:00:00+00:00",
          "feature_rows": 14400,
          "forward_feature_columns_used": false,
          "npz_path": "inputs/v77-first-week/candidate-02-v48-first-week/v48_features.npz",
          "raw_directory": "inputs/v77-first-week/.cache/candidate-02/v77-first-week/binance_1m",
          "raw_first_close_utc": "2024-09-14T00:01:00+00:00",
          "raw_last_close_utc": "2024-09-23T01:00:00+00:00",
          "raw_rows": 13020
        },
        "decision": "REJECT_OR_REDESIGN",
        "effective_commissions_usdt": 5825.22878199,
        "engine": "NautilusTrader 1.230.0",
        "engine_result": {
          "iterations": 13020,
          "stats_general": {},
          "stats_pnls": {
            "USDT": {
              "Avg Loser": -2023.03650522,
              "Avg Winner": 1338.55901835,
              "Expectancy": 591.5377908900001,
              "Max Loser": -3154.1304924300002,
              "Max Winner": 1831.8073872399998,
              "Min Loser": -891.94251801,
              "Min Winner": 644.3022056,
              "PnL (total)": 5323.84011801,
              "PnL% (total)": 5.32384011800999,
              "Win Rate": 0.7777777777777778
            }
          },
          "stats_returns": {
            "Average (Return)": 0.006660606528926666,
            "Average Loss (Return)": -0.014619534798504086,
            "Average Win (Return)": 0.020630980457105375,
            "Profit Factor": 2.8223853551367992,
            "Returns Volatility (252 days)": 0.3000812418238877,
            "Risk Return Ratio": 0.35235075040249236,
            "Sharpe Ratio (252 days)": 5.593394758991919,
            "Sortino Ratio (252 days)": 11.093638491785978
          },
          "summary": {
            "account.BINANCE.balance.USDT.free": "105323.84011801 USDT",
            "account.BINANCE.balance.USDT.locked": "0.00000000 USDT",
            "account.BINANCE.balance.USDT.total": "105323.84011801 USDT",
            "account.BINANCE.base_currency": "USDT",
            "account.BINANCE.event_count": "48",
            "account.BINANCE.id": "BINANCE-001",
            "account.BINANCE.type": "MARGIN",
            "iterations": "13020",
            "orders.closed": "29",
            "orders.emulated": "0",
            "orders.inflight": "0",
            "orders.open": "0",
            "orders.total": "29",
            "positions.closed": "1",
            "positions.open": "0",
            "positions.snapshots": "8",
            "positions.total": "1",
            "positions.total_with_snapshots": "9",
            "total_events": "76",
            "venues.total": "1"
          },
          "total_events": 76,
          "total_orders": 29,
          "total_positions": 9
        },
        "evaluation_days": 7.0,
        "evaluation_end_utc": "2024-09-23T00:00:00+00:00",
        "evaluation_start_utc": "2024-09-16T00:00:00+00:00",
        "execution_model": {
          "account_source_of_truth": true,
          "bar_adaptive_high_low_ordering": true,
          "bar_execution": true,
          "bracket_orders": true,
          "effective_maker_commission_rate": "0.00030",
          "effective_taker_commission_rate": "0.00085",
          "liquidation_enabled": false,
          "liquidation_requested": true
        },
        "fills": 18,
        "final_nav_usdt": 105323.84011800999,
        "flat_at_end": true,
        "geometric_daily_growth_after_cost": 0.007437465773340035,
        "gross_loss_after_cost_usdt": -4046.0730104399845,
        "gross_profit_after_cost_usdt": 9369.913128449974,
        "losses": 2,
        "maximum_effective_notional_multiple": 8.627453026616342,
        "maximum_mark_to_market_drawdown": -0.05584219661450218,
        "maximum_planned_loss_to_budget": 0.9999985690522433,
        "nav_factor": 1.0532384011801,
        "orders": 29,
        "pass_checks": {
          "flat_at_end": true,
          "maximum_drawdown": true,
          "minimum_geometric_daily_growth": false,
          "minimum_profit_factor": true,
          "minimum_trades_per_day": true,
          "minimum_win_rate": true,
          "planned_loss_budget": true
        },
        "positions_rows": 9,
        "profit_factor_after_cost": 2.31580426360894,
        "risk_fraction": 0.03,
        "runtime_diagnostics": {
          "entry_pending": false,
          "position_flat": true,
          "runtime": {
            "ENTRY_BRACKET_SUBMITTED": 9,
            "FORCED_EXIT_MAX_HOLD": 2,
            "POSITION_CLOSED": 9,
            "POSITION_OPENED": 9
          },
          "scheduled_signals": 9,
          "selected_signal": null
        },
        "scheduled_signals": 9,
        "stage": "first_random_btc_week_nautilustrader",
        "starting_nav_usdt": 100000.0,
        "submitted_signals": 9,
        "target_met": false,
        "trades": 9,
        "trades_per_day": 1.2857142857142858,
        "win_rate": 0.7777777777777778,
        "wins": 7
      },
      "metrics_path": "inputs/v77-first-week/artifacts/candidate-02-v77-60m_1x_180/metrics.json",
      "passes_all": false,
      "target_range_multiple": 1.0
    },
    {
      "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1x_240",
      "checks": {
        "drawdown": true,
        "engine": true,
        "growth": false,
        "no_custom_engine": true,
        "profit_factor": true,
        "risk": true,
        "trades_per_day": true,
        "win_rate": true
      },
      "config_path": "research/candidate-02/v77_60m_1x_240_config.json",
      "maximum_holding_minutes": 240,
      "metrics": {
        "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1x_240",
        "custom_backtest_engine": false,
        "data_integrity": {
          "columns_path": "inputs/v77-first-week/candidate-02-v48-first-week/columns.json",
          "feature_first_open_utc": "2024-09-14T00:01:00+00:00",
          "feature_last_open_utc": "2024-09-24T00:00:00+00:00",
          "feature_rows": 14400,
          "forward_feature_columns_used": false,
          "npz_path": "inputs/v77-first-week/candidate-02-v48-first-week/v48_features.npz",
          "raw_directory": "inputs/v77-first-week/.cache/candidate-02/v77-first-week/binance_1m",
          "raw_first_close_utc": "2024-09-14T00:01:00+00:00",
          "raw_last_close_utc": "2024-09-23T01:00:00+00:00",
          "raw_rows": 13020
        },
        "decision": "REJECT_OR_REDESIGN",
        "effective_commissions_usdt": 5840.94000544,
        "engine": "NautilusTrader 1.230.0",
        "engine_result": {
          "iterations": 13020,
          "stats_general": {},
          "stats_pnls": {
            "USDT": {
              "Avg Loser": -1856.8037246099998,
              "Avg Winner": 1376.43082054,
              "Expectancy": 657.9342549511113,
              "Max Loser": -3161.7542803799997,
              "Max Winner": 1842.43444035,
              "Min Loser": -551.85316884,
              "Min Winner": 647.9723976,
              "PnL (total)": 5921.408294559999,
              "PnL% (total)": 5.921408294560009,
              "Win Rate": 0.7777777777777778
            }
          },
          "stats_returns": {
            "Average (Return)": 0.007369379082702743,
            "Average Loss (Return)": -0.026853824123044268,
            "Average Win (Return)": 0.017161771356933243,
            "Profit Factor": 3.1954054808540446,
            "Returns Volatility (252 days)": 0.29681370928259565,
            "Risk Return Ratio": 0.3941370043233923,
            "Sharpe Ratio (252 days)": 6.256730975566113,
            "Sortino Ratio (252 days)": 12.321682701602299
          },
          "summary": {
            "account.BINANCE.balance.USDT.free": "105921.40829456 USDT",
            "account.BINANCE.balance.USDT.locked": "0.00000000 USDT",
            "account.BINANCE.balance.USDT.total": "105921.40829456 USDT",
            "account.BINANCE.base_currency": "USDT",
            "account.BINANCE.event_count": "49",
            "account.BINANCE.id": "BINANCE-001",
            "account.BINANCE.type": "MARGIN",
            "iterations": "13020",
            "orders.closed": "29",
            "orders.emulated": "0",
            "orders.inflight": "0",
            "orders.open": "0",
            "orders.total": "29",
            "positions.closed": "1",
            "positions.open": "0",
            "positions.snapshots": "8",
            "positions.total": "1",
            "positions.total_with_snapshots": "9",
            "total_events": "77",
            "venues.total": "1"
          },
          "total_events": 77,
          "total_orders": 29,
          "total_positions": 9
        },
        "evaluation_days": 7.0,
        "evaluation_end_utc": "2024-09-23T00:00:00+00:00",
        "evaluation_start_utc": "2024-09-16T00:00:00+00:00",
        "execution_model": {
          "account_source_of_truth": true,
          "bar_adaptive_high_low_ordering": true,
          "bar_execution": true,
          "bracket_orders": true,
          "effective_maker_commission_rate": "0.00030",
          "effective_taker_commission_rate": "0.00085",
          "liquidation_enabled": false,
          "liquidation_requested": true
        },
        "fills": 19,
        "final_nav_usdt": 105921.40829456001,
        "flat_at_end": true,
        "geometric_daily_growth_after_cost": 0.00825203357678772,
        "gross_loss_after_cost_usdt": -3713.6074492199987,
        "gross_profit_after_cost_usdt": 9635.015743780008,
        "losses": 2,
        "maximum_effective_notional_multiple": 8.627668991239096,
        "maximum_mark_to_market_drawdown": -0.053628580744285115,
        "maximum_planned_loss_to_budget": 0.9999996451328387,
        "nav_factor": 1.0592140829456,
        "orders": 29,
        "pass_checks": {
          "flat_at_end": true,
          "maximum_drawdown": true,
          "minimum_geometric_daily_growth": false,
          "minimum_profit_factor": true,
          "minimum_trades_per_day": true,
          "minimum_win_rate": true,
          "planned_loss_budget": true
        },
        "positions_rows": 9,
        "profit_factor_after_cost": 2.5945164844506476,
        "risk_fraction": 0.03,
        "runtime_diagnostics": {
          "entry_pending": false,
          "position_flat": true,
          "runtime": {
            "ENTRY_BRACKET_SUBMITTED": 9,
            "FORCED_EXIT_MAX_HOLD": 2,
            "POSITION_CLOSED": 9,
            "POSITION_OPENED": 9
          },
          "scheduled_signals": 9,
          "selected_signal": null
        },
        "scheduled_signals": 9,
        "stage": "first_random_btc_week_nautilustrader",
        "starting_nav_usdt": 100000.0,
        "submitted_signals": 9,
        "target_met": false,
        "trades": 9,
        "trades_per_day": 1.2857142857142858,
        "win_rate": 0.7777777777777778,
        "wins": 7
      },
      "metrics_path": "inputs/v77-first-week/artifacts/candidate-02-v77-60m_1x_240/metrics.json",
      "passes_all": false,
      "target_range_multiple": 1.0
    },
    {
      "candidate": "candidate-02-v76-quarter-hour-metaorder-wave-60m_1x",
      "checks": {
        "drawdown": true,
        "engine": true,
        "growth": false,
        "no_custom_engine": true,
        "profit_factor": true,
        "risk": true,
        "trades_per_day": true,
        "win_rate": true
      },
      "config_path": "research/candidate-02/v76_60m_1x_config.json",
      "maximum_holding_minutes": 120,
      "metrics": {
        "candidate": "candidate-02-v76-quarter-hour-metaorder-wave-60m_1x",
        "custom_backtest_engine": false,
        "data_integrity": {
          "columns_path": "inputs/v77-first-week/candidate-02-v48-first-week/columns.json",
          "feature_first_open_utc": "2024-09-14T00:01:00+00:00",
          "feature_last_open_utc": "2024-09-24T00:00:00+00:00",
          "feature_rows": 14400,
          "forward_feature_columns_used": false,
          "npz_path": "inputs/v77-first-week/candidate-02-v48-first-week/v48_features.npz",
          "raw_directory": "inputs/v77-first-week/.cache/candidate-02/v77-first-week/binance_1m",
          "raw_first_close_utc": "2024-09-14T00:01:00+00:00",
          "raw_last_close_utc": "2024-09-23T01:00:00+00:00",
          "raw_rows": 13020
        },
        "decision": "REJECT_OR_REDESIGN",
        "effective_commissions_usdt": 5859.51486215,
        "engine": "NautilusTrader 1.230.0",
        "engine_result": {
          "iterations": 13020,
          "stats_general": {},
          "stats_pnls": {
            "USDT": {
              "Avg Loser": -1961.37962846,
              "Avg Winner": 1454.0235992528571,
              "Expectancy": 695.0451042055555,
              "Max Loser": -3177.42540005,
              "Max Winner": 1848.11867806,
              "Min Loser": -745.33385687,
              "Min Winner": 650.0368806,
              "PnL (total)": 6255.405937850001,
              "PnL% (total)": 6.25540593785001,
              "Win Rate": 0.7777777777777778
            }
          },
          "stats_returns": {
            "Average (Return)": 0.007769182634865562,
            "Average Loss (Return)": -0.013882323981659561,
            "Average Win (Return)": 0.022479527260560905,
            "Profit Factor": 3.2385827171674455,
            "Returns Volatility (252 days)": 0.30000300581400563,
            "Risk Return Ratio": 0.41110238385090586,
            "Sharpe Ratio (252 days)": 6.5260480263318765,
            "Sortino Ratio (252 days)": 12.9831998363559
          },
          "summary": {
            "account.BINANCE.balance.USDT.free": "106255.40593785 USDT",
            "account.BINANCE.balance.USDT.locked": "0.00000000 USDT",
            "account.BINANCE.balance.USDT.total": "106255.40593785 USDT",
            "account.BINANCE.base_currency": "USDT",
            "account.BINANCE.event_count": "48",
            "account.BINANCE.id": "BINANCE-001",
            "account.BINANCE.type": "MARGIN",
            "iterations": "13020",
            "orders.closed": "29",
            "orders.emulated": "0",
            "orders.inflight": "0",
            "orders.open": "0",
            "orders.total": "29",
            "positions.closed": "1",
            "positions.open": "0",
            "positions.snapshots": "8",
            "positions.total": "1",
            "positions.total_with_snapshots": "9",
            "total_events": "76",
            "venues.total": "1"
          },
          "total_events": 76,
          "total_orders": 29,
          "total_positions": 9
        },
        "evaluation_days": 7.0,
        "evaluation_end_utc": "2024-09-23T00:00:00+00:00",
        "evaluation_start_utc": "2024-09-16T00:00:00+00:00",
        "execution_model": {
          "account_source_of_truth": true,
          "bar_adaptive_high_low_ordering": true,
          "bar_execution": true,
          "bracket_orders": true,
          "effective_maker_commission_rate": "0.00030",
          "effective_taker_commission_rate": "0.00085",
          "liquidation_enabled": false,
          "liquidation_requested": true
        },
        "fills": 18,
        "final_nav_usdt": 106255.40593785001,
        "flat_at_end": true,
        "geometric_daily_growth_after_cost": 0.008705603832076747,
        "gross_loss_after_cost_usdt": -3922.759256919977,
        "gross_profit_after_cost_usdt": 10178.165194769987,
        "losses": 2,
        "maximum_effective_notional_multiple": 8.62794248814777,
        "maximum_mark_to_market_drawdown": -0.04889789824254931,
        "maximum_planned_loss_to_budget": 0.9999970290347502,
        "nav_factor": 1.0625540593785001,
        "orders": 29,
        "pass_checks": {
          "flat_at_end": true,
          "maximum_drawdown": true,
          "minimum_geometric_daily_growth": false,
          "minimum_profit_factor": true,
          "minimum_trades_per_day": true,
          "minimum_win_rate": true,
          "planned_loss_budget": true
        },
        "positions_rows": 9,
        "profit_factor_after_cost": 2.5946443633559992,
        "risk_fraction": 0.03,
        "runtime_diagnostics": {
          "entry_pending": false,
          "position_flat": true,
          "runtime": {
            "ENTRY_BRACKET_SUBMITTED": 9,
            "FORCED_EXIT_MAX_HOLD": 2,
            "POSITION_CLOSED": 9,
            "POSITION_OPENED": 9
          },
          "scheduled_signals": 9,
          "selected_signal": null
        },
        "scheduled_signals": 9,
        "stage": "first_random_btc_week_nautilustrader",
        "starting_nav_usdt": 100000.0,
        "submitted_signals": 9,
        "target_met": false,
        "trades": 9,
        "trades_per_day": 1.2857142857142858,
        "win_rate": 0.7777777777777778,
        "wins": 7
      },
      "metrics_path": "inputs/v77-first-week/artifacts/candidate-02-v77-base120/metrics.json",
      "passes_all": false,
      "target_range_multiple": 1.0
    }
  ],
  "selected_candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_180",
  "selected_config_path": "research/candidate-02/v77_60m_1p25_180_config.json",
  "selected_second_week_start": "2024-01-29",
  "selection_seed": 2026080677,
  "status": "PROMOTED_TO_LOCKED_SECOND_WEEK"
}
```

### `v77_week2_decision.json`

```json
{
  "checks": {
    "drawdown": true,
    "engine": true,
    "growth": false,
    "no_custom_engine": true,
    "profit_factor": false,
    "risk": true,
    "trades_per_day": false,
    "win_rate": false
  },
  "lock": {
    "candidate_family": "candidate-02-v77-quarter-hour-metaorder-wave-reward-duration-plateau",
    "custom_backtest_engine": false,
    "global_pending_entry_plus_position_limit": 1,
    "performance_engine": "NautilusTrader 1.230.0",
    "promotion_requirement": "The unchanged selected rule must pass every original weekly gate before a third week can be selected.",
    "risk_fraction": 0.03,
    "second_week": {
      "end_utc": "2024-02-05T00:00:00Z",
      "raw_data_status_at_lock": "NOT_COLLECTED_FOR_V77",
      "selection_seed": 2026080677,
      "start_utc": "2024-01-29T00:00:00Z",
      "symbol": "BTCUSDT"
    },
    "selected_candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_180",
    "selected_config_sha256": "fadcebd9859e1269631a7012df827f3f76196b348c827560333d276e63605cca",
    "selected_source_config_path": "research/candidate-02/v77_60m_1p25_180_config.json",
    "status": "LOCKED_BEFORE_SECOND_WEEK_COLLECTION"
  },
  "metrics": {
    "candidate": "candidate-02-v77-quarter-hour-metaorder-wave-60m_1p25_180-locked-week2",
    "custom_backtest_engine": false,
    "data_integrity": {
      "columns_path": "inputs/v77-week2/candidate-02-v48-first-week/columns.json",
      "feature_first_open_utc": "2024-01-27T00:01:00+00:00",
      "feature_last_open_utc": "2024-02-06T00:00:00+00:00",
      "feature_rows": 14400,
      "forward_feature_columns_used": false,
      "npz_path": "inputs/v77-week2/candidate-02-v48-first-week/v48_features.npz",
      "raw_directory": "inputs/v77-week2/.cache/candidate-02/v77-week2/binance_1m",
      "raw_first_close_utc": "2024-01-27T00:01:00+00:00",
      "raw_last_close_utc": "2024-02-05T01:00:00+00:00",
      "raw_rows": 13020
    },
    "decision": "REJECT_OR_REDESIGN",
    "effective_commissions_usdt": 3399.23357032,
    "engine": "NautilusTrader 1.230.0",
    "engine_result": {
      "iterations": 13020,
      "stats_general": {},
      "stats_pnls": {
        "USDT": {
          "Avg Loser": -3010.75704885,
          "Avg Winner": 1047.096088115,
          "Expectancy": -1387.615794064,
          "Max Loser": -3100.55889154,
          "Max Winner": 1522.1408488299999,
          "Min Loser": -2955.38146601,
          "Min Winner": 572.0513274,
          "PnL (total)": -6938.07897032,
          "PnL% (total)": -6.9380789703199985,
          "Win Rate": 0.4
        }
      },
      "stats_returns": {
        "Average (Return)": -0.008815920034850253,
        "Average Loss (Return)": -0.028582922922367342,
        "Average Win (Return)": 0.015221408488300003,
        "Profit Factor": 0.17751168567378636,
        "Returns Volatility (252 days)": 0.2740888455803341,
        "Risk Return Ratio": -0.5105949920959323,
        "Sharpe Ratio (252 days)": -8.105444218564964,
        "Sortino Ratio (252 days)": -7.9535561922319165
      },
      "summary": {
        "account.BINANCE.balance.USDT.free": "93061.92102968 USDT",
        "account.BINANCE.balance.USDT.locked": "0.00000000 USDT",
        "account.BINANCE.balance.USDT.total": "93061.92102968 USDT",
        "account.BINANCE.base_currency": "USDT",
        "account.BINANCE.event_count": "28",
        "account.BINANCE.id": "BINANCE-001",
        "account.BINANCE.type": "MARGIN",
        "iterations": "13020",
        "orders.closed": "17",
        "orders.emulated": "0",
        "orders.inflight": "0",
        "orders.open": "0",
        "orders.total": "17",
        "positions.closed": "1",
        "positions.open": "0",
        "positions.snapshots": "4",
        "positions.total": "1",
        "positions.total_with_snapshots": "5",
        "total_events": "44",
        "venues.total": "1"
      },
      "total_events": 44,
      "total_orders": 17,
      "total_positions": 5
    },
    "evaluation_days": 7.0,
    "evaluation_end_utc": "2024-02-05T00:00:00+00:00",
    "evaluation_start_utc": "2024-01-29T00:00:00+00:00",
    "execution_model": {
      "account_source_of_truth": true,
      "bar_adaptive_high_low_ordering": true,
      "bar_execution": true,
      "bracket_orders": true,
      "effective_maker_commission_rate": "0.00030",
      "effective_taker_commission_rate": "0.00085",
      "liquidation_enabled": false,
      "liquidation_requested": true
    },
    "fills": 10,
    "final_nav_usdt": 93061.92102968,
    "flat_at_end": true,
    "geometric_daily_growth_after_cost": -0.010219578279306196,
    "gross_loss_after_cost_usdt": -9032.271146550003,
    "gross_profit_after_cost_usdt": 2094.1921762300044,
    "losses": 3,
    "maximum_effective_notional_multiple": 7.8026855350745725,
    "maximum_mark_to_market_drawdown": -0.09047397590965289,
    "maximum_planned_loss_to_budget": 0.9999844317096732,
    "nav_factor": 0.9306192102968001,
    "orders": 17,
    "pass_checks": {
      "flat_at_end": true,
      "maximum_drawdown": true,
      "minimum_geometric_daily_growth": false,
      "minimum_profit_factor": false,
      "minimum_trades_per_day": false,
      "minimum_win_rate": false,
      "planned_loss_budget": true
    },
    "positions_rows": 5,
    "profit_factor_after_cost": 0.23185665512598225,
    "risk_fraction": 0.03,
    "runtime_diagnostics": {
      "entry_pending": false,
      "position_flat": true,
      "runtime": {
        "ENTRY_BRACKET_SUBMITTED": 5,
        "FORCED_EXIT_MAX_HOLD": 2,
        "POSITION_CLOSED": 5,
        "POSITION_OPENED": 5
      },
      "scheduled_signals": 5,
      "selected_signal": null
    },
    "scheduled_signals": 5,
    "stage": "first_random_btc_week_nautilustrader",
    "starting_nav_usdt": 100000.0,
    "submitted_signals": 5,
    "target_met": false,
    "trades": 5,
    "trades_per_day": 0.7142857142857143,
    "win_rate": 0.4,
    "wins": 2
  },
  "status": "REJECTED_LOCKED_SECOND_WEEK"
}
```

### `v77_terminal_status.json`

```json
{
  "candidate_family": "candidate-02-v77-quarter-hour-metaorder-wave",
  "custom_backtest_engine": false,
  "performance_engine": "NautilusTrader 1.230.0",
  "recorded_utc": "2026-08-06T08:34:10.573902+00:00",
  "risk_fraction": 0.03,
  "snapshots": {
    "first_week": null,
    "long_lock": null,
    "second_week": null,
    "third_week": null
  },
  "status": "NON_TERMINAL_PIPELINE_TIMEOUT_DO_NOT_CLASSIFY_AS_STRATEGY_FAILURE"
}
```

## Interpretation

A first-week plateau, a second-week pass, or a third-week lock is not a completed project candidate. The project is successful only after the unchanged rule passes all three random BTC weeks, a prelocked long evaluation, and the final four-symbol global-single-position evaluation at the fixed 3% risk contract.