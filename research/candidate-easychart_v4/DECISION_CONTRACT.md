# EasyChart v4 decision contract

## Source-explicit rules encoded directly

- Naked price structure and institutional footprints are combined into a
  scenario; no single object is a forecast.
- Trendlines and channels use wick anchors.
- Channel boundaries are exactly parallel.
- A channel needs three confirmed points; the first later interaction is the
  fourth-point opportunity.
- A wick excursion which closes inside is a Fakeout/liquidity sweep.
- A real channel break needs a body close outside and the next candle to open
  outside; breakout entries use a retest.
- Failure to regain the channel midline after a bounce represents weakening.
- OB/FVG at meaningful structure and liquidity refine the entry; the source
  size rule is retained (at least 2× body displacement).
- A planned zone which is not revisited is not chased.
- Stops sit beyond the causal structural invalidation; targets are opposing or
  next structures.

## Human-natural translations needed for code

- Manual magnet-tool anchors become confirmed wick pivots. The pivot is not
  available until its right span has closed.
- Consecutive same-side directional pivots define the current trendline; older
  lines are retained in audit history but removed from the hot decision set.
- One context-bar interaction across overlapping structures is one causal
  episode, not several trades or several independent confirmations.
- A same-displacement OB and FVG are one trigger episode. They do not multiply
  confidence or risk.
- The first retest consumes the opportunity even when its reaction fails.

## Research hypotheses, visible rather than hidden

- The same policy runs on 60m→5m and 15m→1m to cover macro and intraday scenes.
- No fixed trendline-angle threshold is added because the material supplies no
  objective angle boundary. Normalized slope is logged for diagnosis.
- If an accepted channel break has no untouched external pivot objective, one
  channel-width extension is the structural fallback objective.
- The structural event extreme or breakout-leg origin controls the stop;
  a tiny OB/FVG wick does not replace the scenario invalidation.

Every plan records these provenance strings. A weak result must first be
assigned to source understanding, translation, research hypothesis,
implementation, execution, or ordinary probabilistic loss before a rule is
changed.
