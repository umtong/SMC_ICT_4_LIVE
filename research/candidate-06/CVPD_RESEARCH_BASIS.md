# CVPD Research Basis

## Primary empirical and implementation sources

- Carol Alexander and Daniel F. Heck, *Price Discovery, High-Frequency Trading
  and Jumps in Bitcoin Markets*, Journal of Financial Stability 50 (2020),
  article 100776; working-paper record: https://ssrn.com/abstract=3383147.
  The relevant mechanism is that price discovery leadership varies with market
  activity and spot/futures basis dislocations after jumps are rapidly
  arbitraged.  The paper does not validate this candidate's exact rules.

- Peter R. Hansen, Chan Kim and Wade Kimbrough, *Periodicity in Cryptocurrency
  Volatility and Liquidity*, Journal of Financial Econometrics 22(1), 2024,
  https://doi.org/10.1093/jjfinec/nbac034.  This supports normalizing activity
  and recognizing recurrent within-hour structure rather than treating all
  minutes as identically distributed.

- Rama Cont, Arseniy Kukanov and Sasha Stoikov, *The Price Impact of Order Book
  Events*, Journal of Financial Econometrics 12(1), 2014,
  https://doi.org/10.1093/jjfinec/nbt003.  This supports separating aggressive
  flow from realized price progress.  It is not used as a numeric calibration.

- NautilusTrader backtesting documentation,
  https://nautilustrader.io/docs/latest/concepts/backtesting/.  The candidate
  retains close-time bar timestamps, native event ordering, adaptive OHLC path,
  bracket lifecycle, fills and portfolio accounting.

## Official ICT terminology sources

The following videos are used only to preserve the conceptual distinction
between a liquidity event, later market-structure response and intermarket
non-confirmation.  They are not empirical profitability evidence and no spoken
threshold is copied into the program.

- The Inner Circle Trader, *2022 ICT Mentorship Episode 3 – Internal Range
  Liquidity & Market Structure Shifts*, YouTube video `nQfHZ2DEJ8c`.
- The Inner Circle Trader, *ICT Mentorship Core Content – Month 07 – Short Term
  Trading Low Resistance Liquidity Runs Part 1*, YouTube video `y0lVKWLsZeM`.
- The Inner Circle Trader, *ICT Forex – Market Maker Series Vol. 3 of 5*,
  YouTube video `i8xt0EQDjNY`, for the use of correlated-market disagreement as
  contextual evidence rather than a standalone entry.

## Translation into program states

- liquidity sweep: one venue exceeds its own prior completed auction boundary;
- SMT-like non-confirmation: the other venue does not exceed its corresponding
  independently scaled boundary;
- displacement/market-structure response: a later completed perpetual bar
  reclaims or accepts the boundary with aligned body and signed flow;
- draw on liquidity: a prior auction equilibrium/opposite boundary, a
  spot-implied perpetual fair value, or a pre-existing fast/slow pool which still
  clears the structural reward/risk contract;
- invalidation: spot later confirms a supposed perpetual false break, or spot
  loses an accepted break before the perpetual relay completes.
