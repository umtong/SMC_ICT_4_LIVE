# Branch audit provenance

This is the compact, reproducible index for the repository-wide branch audit.
Mechanism-level reasoning remains in `RESEARCH_SYNTHESIS_PROVENANCE.md`; this
file records coverage, immutable tips, and the reuse boundary without repeating
that synthesis.

## Snapshot and reproduction

- Remote: `https://github.com/umtong/SMC_ICT_4_LIVE.git`
- Coordinating audit coverage: **224 remote-head records**.
- Full-ref fetch used for this audit: **2026-08-24T01:10:39+09:00**.
- The latest-50 table below is sorted by committer date and pinned by SHA.
- A repeat live query at **2026-08-24T01:39:48+09:00** advertised 170 heads.
  Remote refs are mutable; therefore 224 is the historical audit coverage, not
  a promise that deleted or transient run heads can still be fetched today.
  The pinned SHAs below, rather than current branch names alone, are the
  provenance anchors.

Recreate the current remote mirror in a disposable namespace:

```bash
git fetch --no-tags https://github.com/umtong/SMC_ICT_4_LIVE.git \
  '+refs/heads/*:refs/remotes/branch-audit/*'

git for-each-ref --sort=-committerdate \
  --format='%(committerdate:iso8601-strict) %(objectname) %(refname:strip=3)' \
  refs/remotes/branch-audit
```

Count the currently advertised heads independently:

```bash
git ls-remote --heads https://github.com/umtong/SMC_ICT_4_LIVE.git
```

The live count can change. A different current count does not retroactively
reduce the 224-head audit scope, and it must not be silently substituted for
the recorded audit inventory.

## Investigation scope

| Family | Scope actually inspected | Audit use |
|---|---|---|
| Numbered candidates | Every existing numbered head: 01-32, 35, 37, 39, 41, 47, 51, 53, 55, 57, 60 and 61; Candidate-16 variants and later continuations were included | Causal auction state, directional and liquidity hypotheses, multi-market topology, negative evidence |
| ML | `ML_a`, ML1, ML2, both ML2 run heads, ML3, ML-thinking and their coherent/directional descendants | Candidate harvesting, causal labels/features, calibration, generalization failures |
| EasyChart | Base family, v2-v26, RE1 and source-faithful verification/run heads | Direction, diagonal/channel structure, OB/FVG location, target ownership and lifecycle |
| Execution | Candidates 01, 05, 10, 18, 20, 29, 35, 51 and the production/execution heads | Nautilus-native orders/accounting, one account/global slot, partial-fill protection, tick clock, continuous replay |
| Results | Committed `RESULT`, `DECISION`, `VERDICT`, metrics/evidence trees, workflow locators and `research_results` bindings | Empirical rejection and provenance; workflow success alone was never counted as alpha |

Absent numbered branch names were not treated as uninspected systems: no such
remote heads existed in the enumerated inventory. Candidate 61 is a continuation
of the Candidate-60 directory, not an independent `research/candidate-61`
implementation.

## Latest 50 lineage tips at the audit boundary

SHA prefixes below are unique in the audit snapshot. The reproduction command
above emits their full object IDs.

### Integrated production and late synthesis

```text
candidate-liquidity-episode-policy-v1              6bed368fc581
research_candidate_4_causal_inventory_transfer     9df00cdf2db3
research_candidate_4t                              36b8eff20317
production-candidate-liquidity-episode-policy-v1   b467a29158b2
research_candidate_2c                              6ad39550b891
research_candidate_3b                              4ede1151d234
research_candidate_1k                              6db0fd91715a
research_causal_liquidity_route_system              072490952822
research_structural_auction_control                  811320edd35f
research_candidate_1a                              95b7370b7235
research_liquidity_episode_policy                    1e87c2d5fce5
```

### Skilled response, direction and control

```text
run_skilled_response_fast_dc_1                       46696071c906
research_skilled_liquidity_response_fast_dc          97d0118cbd02
run_skilled_liquidity_response_v1_1                  0188a47d8604
research_skilled_liquidity_response                  eb95955ae1a5
research_skilled_auction_control                      9277ae9caf2e
research/candidate-easychart_re1                      97b3919e8055
research_directional_liquidity_policy_v2              cb04cfd38291
run_directional_liquidity_policy_v2_1                5cfd88c9e145
```

### Auction and world-model lineage

```text
research_causal_factor_auction_response              a3a0915c71f6
research_liquidity_auction_v8                         a3a0915c71f6
research_liquidity_world_model_v2                     a3afe1350c37
research_liquidity_world_model                        a442ffb809c5
research_liquidity_auction_v6                         3629e9c98515
research_auction_episode_system                       8c9eb935cd0d
research_liquidity_auction_v7                         b913a07f86aa
research_liquidity_auction_v5                         f55b1f4d21e7
research_liquidity_auction_v4                         ab457839e965
research_liquidity_auction_v3                         0bc9cee433d1
research_liquidity_auction                            0ed432adff7b
```

### ML and installer/run lineage

```text
research_ML2                                         a643c01d59e6
research_ML_thinking                                 47e89fec13a8
research_ML3                                         4158de7a289a
ml1-installer-trigger                                4a2717646aae
research_ML1                                         092cf789a451
ml1-installer-trigger2                               752fe0872596
research_ML2-continuation-v2-run                     5cbf4e4e5d00
research_ML2-causal-v2-run                           74eb3bb5fffa
research_skilled_auction_control_easychart_base      2a5dca2adb4b
ML_a                                                 14a27c39e366
```

### EasyChart lineage

```text
research/candidate-easychart                         3abf515e7eff
research/candidate-easychart-v14-simple-contract     a8b19ac082fb
research/candidate-easychart-v26-trendline-ob        3d26414445a6
research/candidate-easychart-v25-direct-mtf-ob       aaf74434fe07
research/candidate-easychart-v24-mtf-ob              93bb34ba8a27
research/candidate-easychart-v23-channel-continuation 5a04b96d8f2b
research/candidate-easychart-v22-hourly-setup        2451e7851b66
research/candidate-easychart-v21-hourly-direction    b8489ccc0ca9
research/candidate-easychart-v20-diagonal-core       6dd4dea3905c
research/candidate-easychart-v19-alternating-horizontal 5f4e36cee3ae
```

## Reuse, exclusion and unresolved boundary

| Decision | Branch evidence boundary | Consequence for the integrated system |
|---|---|---|
| Reuse | Causal pivots and liquidity episodes; failed/accepted auction separation; destination-first geometry; one four-market account/global slot; exact 3% cost-aware sizing; native Nautilus execution; counterfactual/no-trade harvesting | Import the established responsibility, preserving causal and account contracts |
| Exclude as alpha proof | Sparse perfect weeks, high win rate with PF below one, static OI/flow/depth signs, generic ADX/Kaufman regimes, simple lead-lag/symbol ranking, immediate reclaim reversal, delayed confirmation after objective consumption, workflow-only success | Retain as negative evidence or diagnostics; do not promote it as a complete policy |
| Unresolved | Early pre-entry separation of genuine control transfer from common-mode/mechanical completion, with enough unconsumed natural objective and robust cross-regime opportunity density | Requires integrated empirical proof on real causal ledgers; another detector, hard filter, classifier shell or validation gate is not resolution |

## Missing-Piece naming rule

An item already introduced, implemented, tested, or explicitly identified in
any audited branch is **prior art**, even if its implementation failed or was
incomplete. It must not be renamed the “Missing Piece.” Only a demonstrably
absent causal responsibility may receive that label, and promotion still
requires real-data continuous-account evidence. This audit records no claim
that the unresolved item has already been solved.

No performance claim follows from this document. It is a provenance and reuse
boundary, not a backtest result.
