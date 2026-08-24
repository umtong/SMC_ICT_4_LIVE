# EasyChart RE1 operating handoff

## Canonical strategy

- Module: `easychart_re1_bot.py`
- Bundle: `EasyChartRE1BotBundle`
- NautilusTrader strategy: `EasyChartRE1BotStrategy`
- Continuous-account runner: `run_mtf_backtest_re1_bot.py`
- Binance USD-M paper runner: `run_binance_demo_re1.py`

The same bundle and strategy class are used for research, long continuous evaluation and paper/shadow execution. Only data and execution clients change.

## Immutable trading contract

- One account and at most one global position across BTCUSDT, ETHUSDT, SOLUSDT and XRPUSDT.
- Current NAV risk to the original structural invalidation: 3%.
- Quantity is `NAV × 3% / structural stop distance`; costs never shrink it.
- Nautilus receives the quantity-derived effective leverage and Binance Demo is
  set to each contract's supported maximum margin leverage before startup.
- One full-position entry and one full-position exit; no partials or pyramiding.
- Entry, stop and first pre-existing opposing objective are fixed before order submission.
- Gross planned reward/risk must be at least 1.0R.
- No daily loss limit and no trade-count limit.

## Canonical decision sequence

One active liquidity book contains causal DC swings, equal-extreme clusters,
the previous completed four-hour auction and the previous completed UTC day.
Completed 60m/15m structure, active unswept draw, channel location and common
four-market initiative provide context; settlement at the attacked boundary
then gives exactly one episode owner.

The owner follows one of two mutually exclusive paths: sweep/reclaim and fresh
displacement, or body break and next completed 5m outside hold.  Both paths wait
for the first return to an actual FVG, opposite body or the source boundary.
That return must respond on its completed bar or the immediately following
completed minute.  A non-response ends the opportunity.  The event extreme
owns invalidation and the nearest still-unspent opposing liquidity owns the
target; target selection precedes the gross 1R check.

Historical warmup and live Binance bars both preserve exact quote volume, trade
count and taker-buy volume, so replay and paper/shadow use the same flow state.

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

Replay and paper warm and run the same local episode state and four-market
factor state before orders are enabled.
