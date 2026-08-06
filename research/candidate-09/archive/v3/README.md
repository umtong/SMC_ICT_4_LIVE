# Candidate 09 v3 — discarded complete candidate

Reproducible final v3 result commit: `03dec84c4764ea6a9f22840585c735588d57f899`.

V3 tested a continuation-only interpretation of accepted multi-horizon auction breaches. It used completed 60m/240m/1440m extremes, directional approach pressure, outside acceptance, and the first defended retest. Nautilus execution and accounting were clean on the unchanged three BTC weeks, but the economic hypothesis failed decisively:

- pooled daily geometric return: **-1.153545%**
- pooled NAV multiple: **0.783760x**
- baseline trades: **8**, wins: **0**, losses: **8**
- week returns: **-14.1258%, -5.9093%, -2.9996%**
- all three one-variable ablations remained negative

Removing acceptance worsened daily growth to **-1.439760%**. Removing flow produced **-0.909373%**. Entering without the defended-retest confirmation produced **-0.534598%**. Therefore acceptance and retest reduced damage, but the first accepted retest did not establish continuation. Every baseline trade stopped, generally soon after entry.

Failure interpretation: the apparent outside acceptance/first retest was often an inducement or unstable auction, not persistent directional repricing. V4 does not tune the stop or loosen the gate. It waits for either (a) retest plus renewed re-expansion before continuation, or (b) loss of the accepted level with opposite displacement, interpreted as trapped-breakout reversal.
