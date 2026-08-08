# Cross-branch positive alpha components

- candidate branches scanned: `36`
- JSON files scanned: `6281`
- positive route components: `4`
- positive single-family results: `4`

A record appears only when both declared temporal splits have at least three route trades (five for an unsplit single family) and positive after-cost mean return. This is component mining, not a success claim.

## Route components
### Candidate 15 — V28_BREADTH_TREND
- source: `origin/research/candidate-15:research/candidate-15/v29/evidence_locked_router/results/summary.json`
- splits: `development / stability`
- trades: `31 / 18`
- mean net bp: `76.61278268342096 / 116.33569496261434`
- robust score bp: `76.61278268342096`

### Candidate 15 — BREADTH_ALIGNED_6H_TREND
- source: `origin/research/candidate-15:research/candidate-15/v28/adaptive_6h_trend/results/summary.json`
- splits: `development / stability`
- trades: `31 / 18`
- mean net bp: `76.61278268342086 / 116.33569496261424`
- robust score bp: `76.61278268342086`

### Candidate 15 — V26_OI_BUILDUP
- source: `origin/research/candidate-15:research/candidate-15/v29/evidence_locked_router/results/summary.json`
- splits: `development / stability`
- trades: `20 / 19`
- mean net bp: `24.383931131016457 / 28.28849393630499`
- robust score bp: `24.383931131016457`

### Candidate 15 — RESIDUAL_POSITION_BUILDUP_CONTINUATION_2H
- source: `origin/research/candidate-15:research/candidate-15/v26/residual_oi/results/summary.json`
- splits: `development / stability`
- trades: `21 / 20`
- mean net bp: `19.56408133279644 / 39.42989361862582`
- robust score bp: `19.56408133279644`

## Positive single-family results
### Candidate 15
- source: `origin/research/candidate-15:research/candidate-15/v29/evidence_locked_router/results/summary.json`
- location: `$.cross_split_families.V28_BREADTH_TREND.splits`
- trades: `31 / 18`
- mean net bp: `76.61278268342096 / 116.33569496261434`
- robust score bp: `76.61278268342096`

### Candidate 15
- source: `origin/research/candidate-15:research/candidate-15/v28/adaptive_6h_trend/results/summary.json`
- location: `$.cross_split_routes.BREADTH_ALIGNED_6H_TREND.splits`
- trades: `31 / 18`
- mean net bp: `76.61278268342086 / 116.33569496261424`
- robust score bp: `76.61278268342086`

### Candidate 15
- source: `origin/research/candidate-15:research/candidate-15/v29/evidence_locked_router/results/summary.json`
- location: `$.cross_split_families.V26_OI_BUILDUP.splits`
- trades: `20 / 19`
- mean net bp: `24.383931131016457 / 28.28849393630499`
- robust score bp: `24.383931131016457`

### Candidate 15
- source: `origin/research/candidate-15:research/candidate-15/v26/residual_oi/results/summary.json`
- location: `$.cross_split_routes.RESIDUAL_POSITION_BUILDUP_CONTINUATION_2H.splits`
- trades: `21 / 20`
- mean net bp: `19.56408133279644 / 39.42989361862582`
- robust score bp: `19.56408133279644`

