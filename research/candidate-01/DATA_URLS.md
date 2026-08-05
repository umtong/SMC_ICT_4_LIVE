# Candidate 01 public data sources

The runner constructs these Binance Vision USD-M perpetual one-minute kline URLs deterministically. Archives contain OHLCV, quote volume, trade count, and taker-buy volume. The same URL with `.CHECKSUM` appended is requested for publisher integrity verification. Large archives are never committed to Git.

## Frozen random-week months

- `https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2023-06.zip`
- `https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2022-08.zip`
- `https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-11.zip`
- `https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-12.zip`
- `https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2023-02.zip`

## Long-evaluation months

The full suite loads each month from `2024-01` through `2024-12` using:

```text
https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-YYYY-MM.zip
```

Every run's `data_manifest.json` records the exact URL, cache path, byte count, local SHA-256, and publisher SHA-256 when the companion checksum is reachable.
