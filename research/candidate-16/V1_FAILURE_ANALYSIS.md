# Candidate 16 v1 failure analysis

## Evidence identity

- Frozen source: `91317f522546afd837e330a2bde0f9c05e81b068`
- GitHub Actions run: `31246093383`
- Artifact: `candidate-16-v1-screen-91317f522546afd837e330a2bde0f9c05e81b068`
- Evaluation: `2024-05-06` through `2024-05-12` UTC
- Engine: NautilusTrader 1.230.0 `BacktestNode`
- Account: one continuous 100,000 USDT margin account
- Planned risk: current whole-account NAV × 3% per entry

The interval was pre-registered before Candidate 16 output. It is development data from this point onward and must not be reused as untouched evidence.

## Result

**CANDIDATE16_V1_REJECTED**

- ending NAV: `2,521.36862046`
- total return: `-97.478631%`
- geometric daily growth: `-40.889813%`
- closed trades: `167`
- wins / losses: `38 / 129`
- win rate: `22.7545%`
- profit factor: `0.436159`
- expectancy: `-583.704380 USDT/trade`
- realized maximum drawdown: `97.924913%`
- liquidations: `0`
- maximum simultaneous entry intents / positions: `1 / 1`

The failure is decisive. This is not a near miss and it is not a parameter-search invitation.

## Alpha failure attribution

### 1. The failed-auction branch caused the collapse

The router observed:

- 421 parent auctions;
- 172 `FAILED_AUCTION` completions;
- 14 `ACCEPTANCE_CONTINUATION` completions;
- 229 `UNRESOLVED` completions;
- 167 submitted entries.

Closed-scenario attribution was:

| Branch | Trades | Wins | Win rate | Net PnL |
|---|---:|---:|---:|---:|
| REJECTION | 156 | 35 | 22.44% | -96,448.655680 USDT |
| ACCEPTANCE | 2 | 1 | 50.00% | +80.588677 USDT |
| UNKNOWN | 9 | 2 | 22.22% | -1,110.564376 USDT |

The hypothesis `high directional effort + low progress + quick reclaim = failed auction` over-classified ordinary pullbacks, balance noise, and continuing price discovery as reversals.

### 2. The supposedly independent displayed-liquidity channel was recorded but not used

`AuctionObservation.same_side_depth_change_1m` was copied into `latest_depth_response`, but neither the failed-auction condition nor the acceptance condition referenced it. The actual v1 decision used only directional trade flow, price progress, efficiency, outside closes, and close location.

Consequently, price and aggressor flow both defined the state and immediately confirmed the trade. The implementation did not test whether displayed liquidity replenished under repeated attack, whether the attacking queue withdrew, or whether the book remained resilient after the impact.

### 3. Failure observation and reversal initiative were the same economic event

On `FAILED_AUCTION`, `_complete_parent` immediately created a REJECTION setup and called `_submit_entry` on the same completed reclaim bar. There was no later, separately observed opposite initiative.

The state chain was effectively:

```text
attack effort + reclaim
→ label failed auction
→ enter reversal immediately
```

It must instead separate:

```text
attack
→ defending liquidity response
→ completed failure/reclaim
→ later opposite initiative
→ entry
```

### 4. Execution protection also failed, but it does not explain away the alpha failure

There were 41 Nautilus order rejections:

- 32 protective `STOP_MARKET` children were rejected because the next one-minute market fill had already crossed the planned stop, making the stop immediately marketable;
- 9 late reduce-only market exits arrived after the position was already flat.

A rejected protective stop left the position exposed until another exit or the maximum-hold/funding flatten. This produced losses well beyond the 3% planned budget; the worst realized loss was about `-16.37R` relative to its planned risk budget.

However, removing all 32 stop-rejected scenarios does not rescue the hypothesis. The remaining REJECTION sample was:

- 124 trades;
- 22 wins / 102 losses;
- win rate `17.74%`;
- net PnL `-57,113.716279 USDT`.

Therefore:

- unprotected fills are a real execution-integrity defect that must be fixed;
- the failed-auction state classifier is independently and decisively wrong.

### 5. The acceptance branch is not evidence of a viable subsystem

Only two acceptance trades closed, producing one win and one loss for `+80.588677 USDT`. This is neither enough activity nor enough statistical evidence to preserve it as a successful strategy family. Its logic may be reused only as a hypothesis, not as established alpha.

## What is preserved

The following components remain reusable:

- checksum-verified Binance Vision data preparation;
- aggregate-trade and public-depth feature pipeline;
- confirmed-pivot and completed-session liquidity pools;
- one-parent/one-terminal-decision identity;
- explicit `UNRESOLVED` state;
- natural unconsumed-liquidity objectives after costs;
- NautilusTrader order, fill, fee, margin, position, and NAV ownership;
- current-NAV 3% planned-loss sizing;
- one global pending entry or position.

## What is retired

The following v1 policy is retired:

```text
high effort + low progress + boundary reclaim
→ immediate reversal entry
```

No threshold adjustment to `0.14`, `0.24`, `0.42`, session selection, direction selection, or reward/risk is justified by this result.

## Next structural hypothesis

The next candidate must use information with distinct causal roles:

```text
external liquidity interaction
→ aggressive attack is observed
→ defending displayed liquidity replenishes / remains dominant
→ price impact fails to persist and the boundary is reclaimed
→ failure is frozen without an order
→ a later completed bar shows opposite aggressor initiative,
  opposite price progress, and book support in the new direction
→ entry with the full parent excursion as invalidation
→ unconsumed causal liquidity objective after costs
```

True acceptance is the mirror state:

```text
external liquidity interaction
→ attacking flow persists
→ liquidity ahead withdraws / depletes
→ price remains outside on multiple completed observations
→ first defended retest
→ continuation
```

If the displayed-liquidity and initiative evidence disagree, the result is `UNRESOLVED / NO TRADE`.

## Mandatory execution correction

At the actual entry fill, if the protective stop is already crossed or Nautilus rejects the protective child as marketable, the strategy must immediately cancel remaining children and flatten the residual position. It must never remain exposed until a time exit. Late idempotent reduce-only rejections after the account is already flat are recorded separately and do not represent alpha.
