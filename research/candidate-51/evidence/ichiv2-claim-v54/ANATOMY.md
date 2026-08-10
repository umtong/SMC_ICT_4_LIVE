# Public ichiV2 claim reconstruction

- period: 2024-01-01 through 2026-07-24
- cost: 15.0 bp round trip
- risk: current NAV x 3% planned loss
- account: one global slot across BTC, ETH, SOL and XRP
- causal episode: rising edge of one contiguous public source condition
- purpose: mechanism falsification, not promotion

## Policy anatomy

| policy | trades | trades/day | win | PF | mean R | ending NAV | geom/day | max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| public_ichiv2_source | 1658 | 1.771 | 31.9% | 0.785 | -0.012 | 54566.99 | -0.0647% | 46.08% |
| claim_bridge | 2004 | 2.141 | 38.4% | 0.795 | -0.022 | 25049.43 | -0.1478% | 76.06% |
| claim_bridge_roi_only | 1649 | 1.762 | 49.4% | 0.718 | -0.034 | 16335.61 | -0.1934% | 84.30% |

## Exit reason anatomy

| policy | reason | trades | wins | win | pnl | mean R | mean hold min |
|---|---|---:|---:|---:|---:|---:|---:|
| public_ichiv2_source | EMA18_EXIT_SIGNAL | 1648 | 519 | 31.5% | -59888.44 | -0.016 | 84.5 |
| public_ichiv2_source | TRAILING_STOP | 9 | 9 | 100.0% | 11897.06 | 0.570 | 108.9 |
| public_ichiv2_source | TRAILING_STOP_GAP | 1 | 1 | 100.0% | 2558.37 | 1.137 | 120.0 |
| claim_bridge | EMA18_EXIT_SIGNAL | 1257 | 63 | 5.0% | -350137.62 | -0.169 | 54.8 |
| claim_bridge | HARD_STOP | 5 | 0 | 0.0% | -9751.46 | -1.000 | 20.0 |
| claim_bridge | ROI_0.0100 | 341 | 341 | 100.0% | 90977.31 | 0.165 | 59.3 |
| claim_bridge | ROI_0.0100_GAP | 205 | 205 | 100.0% | 91608.32 | 0.269 | 45.0 |
| claim_bridge | ROI_0.0300 | 92 | 92 | 100.0% | 84027.78 | 0.553 | 23.1 |
| claim_bridge | ROI_0.0300_GAP | 4 | 4 | 100.0% | 3765.68 | 0.608 | 10.0 |
| claim_bridge | ROI_0.0500 | 6 | 6 | 100.0% | 10192.21 | 0.942 | 4.2 |
| claim_bridge | ROI_0_CLOCK | 80 | 59 | 73.8% | 4962.44 | 0.041 | 115.0 |
| claim_bridge | ROI_0_TOUCH | 14 | 0 | 0.0% | -595.22 | -0.029 | 123.6 |
| claim_bridge_roi_only | HARD_STOP | 205 | 0 | 0.0% | -275349.91 | -1.000 | 770.3 |
| claim_bridge_roi_only | ROI_0.0100 | 430 | 430 | 100.0% | 89190.52 | 0.165 | 66.1 |
| claim_bridge_roi_only | ROI_0.0100_GAP | 170 | 170 | 100.0% | 59545.52 | 0.268 | 45.0 |
| claim_bridge_roi_only | ROI_0.0300 | 69 | 69 | 100.0% | 50327.17 | 0.553 | 23.0 |
| claim_bridge_roi_only | ROI_0.0300_GAP | 2 | 2 | 100.0% | 1722.16 | 0.607 | 10.0 |
| claim_bridge_roi_only | ROI_0.0500 | 3 | 3 | 100.0% | 4223.68 | 0.942 | 5.0 |
| claim_bridge_roi_only | ROI_0_CLOCK | 210 | 141 | 67.1% | 7181.76 | 0.029 | 115.0 |
| claim_bridge_roi_only | ROI_0_TOUCH | 560 | 0 | 0.0% | -20505.28 | -0.029 | 388.8 |

## Hypothesis assessment inputs

```json
{
  "H1_signal_density": {
    "calendar_days": 936,
    "condition_bars": {
      "BTCUSDT": 848,
      "ETHUSDT": 1861,
      "SOLUSDT": 3281,
      "XRPUSDT": 2747
    },
    "edges_by_symbol": {
      "BTCUSDT": 416,
      "ETHUSDT": 856,
      "SOLUSDT": 1460,
      "XRPUSDT": 1214
    },
    "edges_per_day": 4.215811965811966,
    "independent_edges": 3946,
    "prediction": "materially denser than prior all8/40h implementation"
  },
  "H2_claim_anatomy": {
    "bridge_trades": 2004,
    "ema_exit_pnl": -350137.619488073,
    "ema_exit_trades": 1257,
    "ema_exit_win_rate": 0.050119331742243436,
    "prediction": "ROI-dominated winner engine with loss-heavy EMA exits",
    "roi_share": 0.37025948103792417,
    "roi_trades": 742
  },
  "H3_exit_repair": {
    "bridge_gross_loss": 365038.06707417784,
    "bridge_mean_r": -0.022129386196231917,
    "falsification": "EMA losses migrate to hard stops/long holds without winner preservation",
    "paired": {
      "common_signal_ids": 1584,
      "ema_exit_common": 998,
      "ema_exit_mean_r_bridge": -0.17243293636860407,
      "improved_common_share": 0.5801767676767676,
      "mean_r_change_no_exit_minus_bridge": -0.009264569050114132,
      "median_r_change_no_exit_minus_bridge": 5.551115123125783e-17,
      "same_signal_mean_r_no_exit": -0.18713742271668096,
      "same_signal_stop_share_no_exit": 0.1963927855711423,
      "worsened_common_share": 0.25757575757575757
    },
    "roi_only_gross_loss": 297128.8191021393,
    "roi_only_mean_r": -0.0342661612748622
  },
  "H4_regime_robustness": {
    "bridge_positive_quarter_share": 0.09090909090909091,
    "bridge_quarters": [
      {
        "mean_r": -0.015459071962896814,
        "pnl": -13135.527724673633,
        "policy": "claim_bridge",
        "profit_factor": 0.8549979939281164,
        "quarter": "2024-Q1",
        "trades": 285,
        "win_rate": 0.41754385964912283,
        "wins": 119
      },
      {
        "mean_r": -0.051021222719260774,
        "pnl": -19928.89039988672,
        "policy": "claim_bridge",
        "profit_factor": 0.5672679505921493,
        "quarter": "2024-Q2",
        "trades": 168,
        "win_rate": 0.34523809523809523,
        "wins": 58
      },
      {
        "mean_r": -0.016190988743829647,
        "pnl": -6428.993999121665,
        "policy": "claim_bridge",
        "profit_factor": 0.8372076121130118,
        "quarter": "2024-Q3",
        "trades": 198,
        "win_rate": 0.3939393939393939,
        "wins": 78
      },
      {
        "mean_r": -0.02127480891712259,
        "pnl": -12336.019367516486,
        "policy": "claim_bridge",
        "profit_factor": 0.8341943761831702,
        "quarter": "2024-Q4",
        "trades": 336,
        "win_rate": 0.38095238095238093,
        "wins": 128
      },
      {
        "mean_r": -0.02409787225612342,
        "pnl": -8727.036751805068,
        "policy": "claim_bridge",
        "profit_factor": 0.7951504787204611,
        "quarter": "2025-Q1",
        "trades": 265,
        "win_rate": 0.39245283018867927,
        "wins": 104
      },
      {
        "mean_r": -0.007588670783992242,
        "pnl": -1831.4823408668765,
        "policy": "claim_bridge",
        "profit_factor": 0.918730851902397,
        "quarter": "2025-Q2",
        "trades": 186,
        "win_rate": 0.3978494623655914,
        "wins": 74
      },
      {
        "mean_r": -0.027519215516819802,
        "pnl": -4988.6420333041015,
        "policy": "claim_bridge",
        "profit_factor": 0.6933587669118007,
        "quarter": "2025-Q3",
        "trades": 169,
        "win_rate": 0.35502958579881655,
        "wins": 60
      },
      {
        "mean_r": -0.0066245723052932096,
        "pnl": -995.9403136716862,
        "policy": "claim_bridge",
        "profit_factor": 0.918572349822802,
        "quarter": "2025-Q4",
        "trades": 143,
        "win_rate": 0.44755244755244755,
        "wins": 64
      },
      {
        "mean_r": -0.03677633117752081,
        "pnl": -4686.830624863449,
        "policy": "claim_bridge",
        "profit_factor": 0.6355057504534728,
        "quarter": "2026-Q1",
        "trades": 143,
        "win_rate": 0.3356643356643357,
        "wins": 48
      },
      {
        "mean_r": -0.03256593262128735,
        "pnl": -2256.3566978352974,
        "policy": "claim_bridge",
        "profit_factor": 0.6646786441056727,
        "quarter": "2026-Q2",
        "trades": 88,
        "win_rate": 0.3181818181818182,
        "wins": 28
      },
      {
        "mean_r": 0.021937833095547987,
        "pnl": 365.15187252960845,
        "policy": "claim_bridge",
        "profit_factor": 1.2856923907490667,
        "quarter": "2026-Q3",
        "trades": 23,
        "win_rate": 0.391304347826087,
        "wins": 9
      }
    ],
    "prediction": "opportunity and expectancy survive multiple quarters",
    "roi_only_positive_quarter_share": 0.09090909090909091,
    "roi_only_quarters": [
      {
        "mean_r": -0.06968648163896565,
        "pnl": -36804.10448858144,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.5627107294113781,
        "quarter": "2024-Q1",
        "trades": 211,
        "win_rate": 0.47393364928909953,
        "wins": 100
      },
      {
        "mean_r": -0.07924711451207783,
        "pnl": -18782.466983087015,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.46106445359206877,
        "quarter": "2024-Q2",
        "trades": 144,
        "win_rate": 0.4027777777777778,
        "wins": 58
      },
      {
        "mean_r": 0.015083754345044685,
        "pnl": 2935.6560650004376,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 1.1477318991557344,
        "quarter": "2024-Q3",
        "trades": 159,
        "win_rate": 0.49056603773584906,
        "wins": 78
      },
      {
        "mean_r": -0.0017830876272970707,
        "pnl": -1737.1493801186125,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.9706454009686525,
        "quarter": "2024-Q4",
        "trades": 272,
        "win_rate": 0.5477941176470589,
        "wins": 149
      },
      {
        "mean_r": -0.06131124430531783,
        "pnl": -15867.526106939642,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.6279416381622067,
        "quarter": "2025-Q1",
        "trades": 222,
        "win_rate": 0.5135135135135135,
        "wins": 114
      },
      {
        "mean_r": 0.0001266703517379013,
        "pnl": -270.94590584036223,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.9837731499457077,
        "quarter": "2025-Q2",
        "trades": 161,
        "win_rate": 0.515527950310559,
        "wins": 83
      },
      {
        "mean_r": -0.04321980417177138,
        "pnl": -5260.103586189992,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.6327361870939872,
        "quarter": "2025-Q3",
        "trades": 145,
        "win_rate": 0.4827586206896552,
        "wins": 70
      },
      {
        "mean_r": -0.024658535531005724,
        "pnl": -2246.104423400941,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.7779338587939465,
        "quarter": "2025-Q4",
        "trades": 122,
        "win_rate": 0.5655737704918032,
        "wins": 69
      },
      {
        "mean_r": -0.06038900882210434,
        "pnl": -4473.438010695495,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.5713564046036563,
        "quarter": "2026-Q1",
        "trades": 121,
        "win_rate": 0.47107438016528924,
        "wins": 57
      },
      {
        "mean_r": -0.013168829847741478,
        "pnl": -570.9606255967939,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.8460714821036655,
        "quarter": "2026-Q2",
        "trades": 75,
        "win_rate": 0.41333333333333333,
        "wins": 31
      },
      {
        "mean_r": -0.06718455164873817,
        "pnl": -587.2423354171705,
        "policy": "claim_bridge_roi_only",
        "profit_factor": 0.48311483937832056,
        "quarter": "2026-Q3",
        "trades": 17,
        "win_rate": 0.35294117647058826,
        "wins": 6
      }
    ]
  }
}
```

## Interpretation contract

This artifact diagnoses one external reconstruction.  It is not a strategy promotion.  A promising mechanism must be frozen and executed in NautilusTrader with actual order lifecycle, funding, adverse slippage, one global position, current-NAV risk sizing and continuous account state.
