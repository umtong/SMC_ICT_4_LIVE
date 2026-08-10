# Candidate 57 — EdgeBot 4σ rolling-VWAP mean reversion v1

## External discovery signal

EdgeBot Lab publicly describes a live BTC/USDT 15-minute strategy, `mr_meanrev_v3`, that enters on a four-standard-deviation displacement from a 20-period VWAP and exits at the mean. The page reports 1,847 trades over fourteen live months, 62.3% wins, Sharpe 1.84 and maximum drawdown 8.2%.

The public page does not expose the exact dispersion estimator, stop, order type, repeat-entry policy, costs or trade ledger through the available interface. The claim is therefore a high-information discovery signal, not project evidence.

## Reconstruction boundary

The phrase “4σ deviation from the 20-period VWAP” admits two materially different causal implementations. Both are predeclared instead of silently choosing the better result.

1. `weighted_band`: rolling VWAP of HLC3 over twenty completed 15-minute candles and TradingView-style volume-weighted dispersion, `sqrt(sum(V*P^2)/sum(V)-VWAP^2)`.
2. `prior_residual`: current completed-candle HLC3 minus rolling VWAP, normalized by the standard deviation of the prior twenty completed VWAP residuals. The current residual is excluded from its own scale.

A long candidate requires a completed price below -4σ; a short candidate requires above +4σ. The signal is a completed-candle level. Re-entry on a later source candle is allowed only after the account is flat.

## Mean and risk interpretations

### Mean target

- `static_entry_mean`: objective is the VWAP known at entry.
- `dynamic_mean`: no reachable fixed objective; exit is requested only when a later completed 15-minute close crosses the then-current rolling VWAP.

### Invalidation

- `six_sigma`: stop at the same entry-time VWAP ±6σ.
- `impulse_extreme`: stop beyond the displacement candle extreme with causal ATR/minimum-distance buffer.

The stop is not a nominal cap. Quantity remains current NAV ×3% divided by the full expected loss per unit including fees, slippage and funding reserve.

## Frozen cells

1. `btc_weighted_6sigma_static` — closest public-scope interpretation.
2. `all_weighted_6sigma_static` — same mechanism generalized to the project universe.
3. `all_weighted_impulse_static` — same signal with auction invalidation.
4. `all_prior_residual_impulse_static` — alternative causal meaning of sigma.
5. `all_weighted_impulse_dynamic` — moving mean exit.

No 3σ/3.5σ/4.5σ grid is run. The public 4σ and 20-period values remain fixed.

## Evaluation allocation

- Development: 2026-01-01 through 2026-01-14.
- Independent comparison account: 2025-05-05 through 2025-05-11.
- Conditional 30-day continuous account: 2024-09-01 through 2024-09-30.

Every case persists every completed trade, exit family, R distribution, symbol/side result and winner-versus-loser entry-state contrast. Development reserves one comparison slot for quality and one for opportunity density when distinct. This is information-value allocation, not a binary truth claim. The 30-day account is consumed only for a positive comparison survivor.

Strict final pass remains one continuous four-symbol account with after-cost geometric daily growth at least 1%, completed trades at least calendar days, positive expectancy, profit factor above one or no losses, maximum drawdown at most 20%, no liquidation and valid one-slot mechanics.
