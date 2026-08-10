# ZaratustraV5 all-candidate forensic v4

The Nautilus account is unchanged; every shadow episode is non-trading.

- baseline identical: True
- actual trades: 214
- raw source signals: 8200
- independent continuous episodes: 1125
- collision boundaries: 227

## Shadow episode groups

| group | episodes | positive/negative | mean net R | PF(R) | mean MFE R | mean MAE R |
|---|---:|---:|---:|---:|---:|---:|
| router_selected | 498 | 353/145 | -0.02225709147953109 | 0.8526275952994952 | 0.262078424626678 | 0.3327404807730768 |
| router_rejected | 622 | 402/220 | -0.05854906720465619 | 0.6561403773970899 | 0.24100422516436265 | 0.33533873798100555 |
| flat_start | 180 | 126/54 | -0.024921726190903114 | 0.8322863329876535 | 0.2529029422737459 | 0.31577476011563654 |
| blocked_start | 940 | 629/311 | -0.04576131983376603 | 0.7213502224896343 | 0.24989058926281382 | 0.3377085081152799 |

## Collision arbitration

- selected best share: 0.4801762114537445
- least-score best share: 0.41409691629955947
- common-mode all-negative share: 0.2555066079295154
- mean selected minus least-score R: 0.014758270236213712
- median selected minus least-score R: 0.0
- selected worse than least-score share: 0.4581497797356828
- mean within-boundary score/return correlation: 0.08214508861470206

## Predeclared interpretation

- max-extension arbitration hypothesis supported: False
- reason: collisions=227; selected-best=0.480; selected-minus-least=0.015R; selected-worse-share=0.458; common-mode-loss-share=0.256

No arbitration policy is changed by this audit. An untouched minimum-score comparison is justified only if the repeated collision evidence shows a broad rank effect rather than a few outliers. If common-mode losses dominate, the missing component is a market-wide state classifier, not symbol ranking.