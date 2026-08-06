# Candidate 09 v4 — positive structural path, not a complete pass

Reproducible Nautilus run: GitHub Actions run `31105658703`; engine SHA-256 `5b7c5873efae27b0c395f57b541449247f6b6596725daff28a43fb0588923171`.

V4 resolved an accepted liquidity breach in two ways: trapped-breakout reversal after loss of the accepted level, or continuation only after defended retest and re-expansion. On the unchanged three BTC weeks it was the first candidate with positive pooled performance:

- pooled daily geometric return: **+0.243439%**
- pooled NAV multiple: **1.052422x**
- trades: **10**, wins: **5**, losses: **5**
- week returns: **+8.0446%, -3.0008%, +0.4163%**
- maximum segment drawdown: **6.1646%**

The gate still failed: fewer than five trades per week, one negative week, daily growth below 1%, and winner concentration. Component accounting was decisive:

- both continuation trades lost approximately **-6,106.20 USDT** in total;
- trapped-breakout reversals were net positive; removing continuation from the recorded path implies approximately **1.1185x** pooled NAV and **+0.535% daily geometric growth** before an exact controlled rerun;
- all three observed 240-minute reversal trades lost, while 15m/60m/1440m reversal trades were positive in this small sample.

V4 is not labeled a success. V5 preserves the accepted-breakout-failure mechanism, removes the repeatedly failed continuation branch, excludes 240m from baseline, adds causal completed 5m auctions for opportunity, and targets the opposite edge of the already completed source range. Each new component is tested by a one-variable ablation.
