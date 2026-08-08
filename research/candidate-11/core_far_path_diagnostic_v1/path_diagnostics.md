# Candidate 11 core FAR path diagnostic

**TEMPORARY_TEST — cannot advance the candidate or claim alpha**

- trades: `9`
- wins / losses: `1 / 8`
- records complete: `True`
- median losing MFE: `0.8525044494656036` structural R
- median losing target progress: `0.45875465872255683`
- losses below 0.25R: `1`
- losses below 0.5R: `2`
- losses whose reclaimed pool failed before 0.5R: `4`
- losses whose unanimous peer support broke within five minutes: `8`

## Trade paths

- SOLUSDT-PERP.BINANCE-LONDON_CLOSE_1000_1200_NY-R000261-LOW: TAKE_PROFIT, MFE=2.4844R, target_progress=0.9983, class=TARGET_DELIVERED
- SOLUSDT-PERP.BINANCE-LONDON_CLOSE_1000_1200_NY-R000345-HIGH: STOP_LOSS, MFE=0.6733R, target_progress=0.4591, class=PARTIAL_DELIVERY_THEN_FAILURE
- BTCUSDT-PERP.BINANCE-LONDON_CLOSE_1000_1200_NY-R000121-LOW: STOP_LOSS, MFE=2.7268R, target_progress=0.4584, class=ONE_R_OR_MORE_THEN_FAILURE
- XRPUSDT-PERP.BINANCE-US_LATE_1600_2000_NY-R000152-LOW: STOP_LOSS, MFE=0.5874R, target_progress=0.1894, class=PARTIAL_DELIVERY_THEN_FAILURE
- SOLUSDT-PERP.BINANCE-NY_PREMARKET_0500_0700_NY-R000426-HIGH: STOP_LOSS, MFE=2.8596R, target_progress=0.6551, class=ONE_R_OR_MORE_THEN_FAILURE
- ETHUSDT-PERP.BINANCE-LONDON_0200_0500_NY-R000481-HIGH: STOP_LOSS, MFE=0.3637R, target_progress=0.1533, class=MINOR_DELIVERY_THEN_FAILURE
- ETHUSDT-PERP.BINANCE-LONDON_CLOSE_1000_1200_NY-R000233-HIGH: STOP_LOSS, MFE=2.6441R, target_progress=0.5572, class=ONE_R_OR_MORE_THEN_FAILURE
- XRPUSDT-PERP.BINANCE-ASIA_2000_0000_NY-R000505-HIGH: STOP_LOSS, MFE=0.1323R, target_progress=0.0163, class=NO_MEANINGFUL_POST_ENTRY_DELIVERY
- SOLUSDT-PERP.BINANCE-ASIA_2000_0000_NY-R000813-LOW: STOP_LOSS, MFE=1.0317R, target_progress=0.6066, class=ONE_R_OR_MORE_THEN_FAILURE

## Interpretation limits

- Thresholds such as 0.25R and 0.5R classify paths only; they are not admission parameters.
- This diagnostic cannot delete losing scenarios or authorize a new candidate.
- A next candidate is allowed only if one market-state assumption is replaced causally.
