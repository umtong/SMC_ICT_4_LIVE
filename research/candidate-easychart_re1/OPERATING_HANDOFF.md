# EasyChart RE1 operating handoff

## Canonical strategy

- Module: `easychart_re1_bot.py`
- Bundle: `EasyChartRE1BotBundle`
- NautilusTrader strategy: `EasyChartRE1BotStrategy`
- Continuous-account runner: `run_mtf_backtest_re1_bot.py`

The same bundle and strategy class are used for research, long continuous evaluation and paper/shadow execution. Only data and execution clients change.

## Immutable trading contract

- One account and at most one global position across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.
- Current NAV risk to the original structural invalidation: 3%.
- One full-position entry and one full-position exit; no partials or pyramiding.
- Entry, stop and first pre-existing opposing objective are fixed before order submission.
- Gross planned reward/risk must be at least 1.0R.
- No daily loss limit and no trade-count limit.

## Integrated auction owners

1. Responsible rejection/reversal.
2. Event-local OB/FVG continuation.
3. Horizontal S/R flip.
4. Contextual local efficient pullback.
5. Residual macro-trend efficient pullback.
6. Residual mature diagonal/channel acceptance.

Continuation requires a body break, the immediate next decision bar to hold outside, the first later return and the first completed micro response. A causal episode has one owner. Local continuations align with accepted 60-minute direction unless a live BTC/ETH-led common impulse supports the faster transition.

## Long continuous command

```bash
python research/candidate-easychart_re1/run_mtf_backtest_re1_bot.py \
  --start 2024-01-01 \
  --end 2026-07-31 \
  --warmup-days 30 \
  --symbols BTCUSDT ETHUSDT SOLUSDT XRPUSDT \
  --cache .cache/candidate-easychart-re1-bot-long \
  --output artifacts/candidate-easychart-re1-bot-long \
  --fee-profile usd_m_vip0
```

Do not split and add periods. Treat the four symbols, arbitration, account NAV and one-position constraint as one continuous result.

## Paper/shadow observations

Record planned, accepted and filled entry/stop/target prices; fee and slippage differences; rejected and partial orders; signal-close to order-accept latency; sibling stop/target cancellation; restart recovery; open-position/protective-order reconciliation; and the NAV used by every 3% risk calculation. Do not alter strategy rules from a paper outcome before reviewing the entire causal episode.
