# ADXStochastic directional-state forensic v2

Both accounts are behaviour-identical replays of already consumed data.

## development

- baseline identical: True
- trades: 12
- wins/losses: 4/8
- PF: 0.21763777916770613

| outcome | trades | mean R | entry bullish DMI | bullish state before outcome boundary | negative pressure weakening before boundary | entry DMI spread mean | ADX slope(3) mean |
|---|---:|---:|---:|---:|---:|---:|---:|
| winner | 4 | 0.23055873362128526 | 0.0 | 0.0 | 0.5 | -35.604516596637794 | 5.22838060717535 |
| partial_loss | 4 | -0.09352052048198005 | 0.0 | 0.0 | 0.5 | -24.63025276565228 | 1.3659725732013364 |
| full_stop | 4 | -0.9550290165567981 | 0.25 | 0.25 | 0.5 | -29.06053706821973 | 1.1819624985833883 |

## reserved

- baseline identical: True
- trades: 5
- wins/losses: 5/0
- PF: None

| outcome | trades | mean R | entry bullish DMI | bullish state before outcome boundary | negative pressure weakening before boundary | entry DMI spread mean | ADX slope(3) mean |
|---|---:|---:|---:|---:|---:|---:|---:|
| winner | 5 | 0.10934834542422503 | 0.4 | 0.4 | 0.6 | -1.3326749070714796 | -1.8399821351062524 |
| partial_loss | 0 | None | None | None | None | None | None |
| full_stop | 0 | None | None | None | None | None | None |

## Predeclared interpretation

- directional-state hypothesis supported: False
- reason: development winner bullish-before=0.0; development full-stop bullish-before=0.25; reserved winner bullish-before=0.4

A directional confirmation policy is not implemented by this workflow. It is justified only if the same temporal ordering explains both the losing development episodes and the winning reserved episodes without consuming the objective before entry.