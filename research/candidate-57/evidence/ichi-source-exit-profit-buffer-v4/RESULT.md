# Ichi source-exit profit-buffer v4 policy-fresh result

- interval: 2025-02-01 to 2025-02-28
- mechanically valid: True
- decision: `POLICY_FRESH_HYPOTHESIS_REJECTED_NO_RETUNING`
- thresholds searched: False
- integration authorized: False
- long evaluation authorized: False

| case | trades | W/L | PF | expectancy USDT | geo/day | return | MDD |
|---|---:|---:|---:|---:|---:|---:|---:|
| source control | 116 | 51/65 | 0.9691442567317128 | -17.44876436879311 | -0.0007300267451239018 | -0.020240566667800053 | 0.16482278181184395 |
| profit buffer | 116 | 49/67 | 0.9565060367632896 | -24.701066793534498 | -0.0010377381581322398 | -0.02865323748049997 | 0.17200088937853397 |

## Frozen causal effect

- arms: 5
- resolutions: {'ichi_profit_buffer_disarms': 1, 'ichi_profit_buffer_immediate_nonpositive_exits': 60, 'ichi_profit_buffer_break_even_exits': 3, 'ichi_profit_buffer_confirmed_exits': 1, 'ichi_profit_buffer_roi_resolutions': 0}
- return delta: -0.008412670812699918
- geo/day delta: -0.00030771141300833804
- expectancy delta USDT: -7.252302424741387
- MDD delta: 0.007178107566690017
- paired trades: 116
- changed paired trades: 25
- control-only trades: 0
- candidate-only trades: 0

The result is interpreted by the predeclared transaction-level prediction, not by an aggregate pass/fail gate.  A rejection closes this exact lifecycle repair without threshold or hold-time retuning.
