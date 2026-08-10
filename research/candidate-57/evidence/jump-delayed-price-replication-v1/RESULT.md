# Two-bar jump confirmation — multi-regime replication

Each interval is a separate continuous account. Returns are not concatenated or compounded across the gaps.

| interval | cell | trades | W/L | PF | geo/day | total return | MDD | selected symbols |
|---|---|---:|---:|---:|---:|---:|---:|---|
| february_2025 | immediate_control | 10 | 5/5 | 1.3029095787519434 | 0.002039892081954653 | 0.028940261831400083 | 0.0867698079307142 | null |
| february_2025 | two_bar_price_confirmation | 8 | 3/5 | 0.14133039805786038 | -0.006540493108810441 | -0.08777413629370001 | 0.1043610389133538 | null |
| july_2025 | immediate_control | 17 | 3/14 | 0.2301118143684121 | -0.019037513589513888 | -0.23592921547459988 | 0.2741109701842619 | null |
| july_2025 | two_bar_price_confirmation | 10 | 1/9 | 0.01325935480004921 | -0.014825106866885984 | -0.18869024786639998 | 0.21186437644837752 | null |
| january_2026 | immediate_control | 9 | 1/8 | 0.0069186651880386325 | -0.010439636498511762 | -0.13663968848630004 | 0.16229919323423359 | null |
| january_2026 | two_bar_price_confirmation | 5 | 0/5 | 0.0 | -0.008921043860787625 | -0.11790458167999995 | 0.15062770497461964 | null |

## Replication summary

- delayed positive accounts: 0 / 3
- delayed completed trades: 23
- replication rule pass: False
