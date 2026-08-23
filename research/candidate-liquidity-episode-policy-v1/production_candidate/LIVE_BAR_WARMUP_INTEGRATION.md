# Live bar warm-up integration contract

The connected runner should import
`smc_ict_4.episode_policy_live.live_bars.fetch_recent_binance_bars` and pass
its result to the existing immutable SQLite bootstrap path. The default call:

```python
bars = fetch_recent_binance_bars(symbol)
```

returns exactly 10,080 sorted, unique and contiguous completed one-minute
`PolicyBar` objects (seven days). `limit=` changes the requested total history;
it is not an HTTP page size. The implementation issues backward Binance USD-M
public `GET /fapi/v1/klines` pages of at most 1,500 rows, without credentials.

Integration requirements:

1. Sample all four symbols through this API before replaying the stored bars
   into `LiquidityEpisodeCoordinator`.
2. Preserve the existing stored-minute equality/mutation check when appending.
3. Do not catch `BinanceBarConflictError`, `BinanceBarGapError`, or
   `BinanceBarDataError` and continue with partial history. Startup must remain
   failed until a complete causal window can be obtained.
4. Keep the returned exclusive clock (`close_time_ns == open_time_ns + 60s`);
   do not convert it back to Binance's inclusive final millisecond.
5. Expose the requested history length in configuration, with 10,080 as the
   connected default. A short diagnostic call may explicitly use a lower
   `limit`, but must not be described as policy-ready warm-up.

The startup clock is frozen before the first HTTP request. Therefore a bar
which completes while pagination is running is intentionally excluded from
the warm-up and can arrive later through the normal connected market stream.
