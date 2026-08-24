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

The bot follows one channel-liquidity episode from the fourth-point sweep through
reclaim, the next completed five-minute hold and the first valid return.  A
causally formed OB/FVG owns its future first return.  When no visual footprint
formed, absorption cannot enter directly: a completed five-minute control
transfer must reclaim the boundary and interaction balance without a new
adverse extreme, then the first later boundary return must close on the intended
side.  The original sweep extreme owns invalidation and the first pre-existing
opposing objective owns the target.

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

## Paper/shadow observations

Record planned, accepted and filled entry/stop/target prices; fee and slippage differences; rejected and partial orders; signal-close to order-accept latency; sibling stop/target cancellation; restart recovery; open-position/protective-order reconciliation; and the NAV used by every 3% risk calculation. Do not alter strategy rules from a paper outcome before reviewing the entire causal episode.
