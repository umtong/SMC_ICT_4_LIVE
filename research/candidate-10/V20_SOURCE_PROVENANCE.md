# Candidate 10 v20 Source Provenance

## Purpose

This document fixes an implementation/provenance error without changing the v20 market hypothesis, parameters, data windows, costs, risk sizing, or ablation.

## Canonical source

The canonical v20 source is the archive committed when v20 was first introduced:

- commit: `f3d68f941637f23b40c4e3e29345669600be7f54`
- commit message: `candidate-10: add v20 liquidation auction candidate`
- path: `research/candidate-10/v20_source.zip`
- Git blob: `32114dbd1f555e9a417c2774073abf025a15e3c2`
- Git-recorded size: `15135` bytes

The same blob is present in the v20.1 cost-control commit `443b739149ee1b3904142d8a234d828e1d9f53ef`. This confirms that v20.1 changed the execution/cost-control overlay rather than silently replacing the underlying hypothesis source.

## Error classification

Later commits replaced `v20_source.zip` with another blob while workflow documentation continued to expect an unrelated local raw SHA-256. The affected workflows stopped before source extraction or NautilusTrader execution. Therefore these runs are implementation failures, not zero-trade strategy results and not logic failures.

The failed pre-strategy runs include:

1. job-container checkout file-command permission failure;
2. nested heredoc shell parsing failure;
3. raw archive SHA mismatch caused by the broken provenance contract.

No performance conclusion is derived from them.

## Controlled recovery

The v20.2 workflow now:

1. checks out full Git history;
2. verifies that the first-v20 commit path resolves to Git blob `32114dbd...`;
3. reconstructs the archive directly with `git cat-file blob`;
4. validates the reconstructed ZIP and records its raw SHA-256, size, and members in `source_provenance.json`;
5. validates the independent v20.2 patch archive;
6. runs `smc4 doctor`, compilation, unit tests, full/no-OI exact ablation, NautilusTrader orders, accounting, and NAV evaluation under the original fixed-week contract.

The currently tracked replacement `v20_source.zip` is not used by this controlled workflow. This prevents a binary contents-API rewrite from altering the research object.

## Research invariants preserved

- BTC first gate week: `2023-10-16`
- preselected later weeks: `2023-05-15`, `2024-01-15`
- strategy: liquidity-pool liquidation auction rejection/acceptance
- exact ablation: remove only OI state
- current whole-account NAV sizing
- maximum planned loss: 3% of NAV per trade
- no arbitrary nominal cap or score-based risk multiplier
- costs and size-dependent impact included
- all order matching, fills, positions, fees, margin and NAV accounting remain in NautilusTrader

## Promotion rule

A clean run is evaluated as strategy evidence only after source lineage, compilation, tests, data manifests, event causality, order lifecycle, and accounting all pass. The first week must show sufficient independent opportunities and strong cost-after NAV growth before later weeks or long evaluation are run.
