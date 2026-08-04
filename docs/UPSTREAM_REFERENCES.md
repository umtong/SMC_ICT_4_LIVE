# NautilusTrader 기준 자료

공통 기반은 NautilusTrader `1.230.0`의 공식 문서·코드 예제를 기준으로 작성했습니다.

- High-level backtest: https://nautilustrader.io/docs/latest/getting_started/backtest_high_level/
- Quickstart: https://nautilustrader.io/docs/latest/getting_started/quickstart/
- Data model: https://nautilustrader.io/docs/latest/concepts/data/
- Testing: https://nautilustrader.io/docs/latest/developer_guide/testing/
- PyPI release: https://pypi.org/project/nautilus_trader/1.230.0/
- Source tag: https://github.com/nautechsystems/nautilus_trader/tree/v1.230.0

`BacktestNode`와 `ParquetDataCatalog`가 config 기반 연구의 공식 권장 경로입니다. `smc4 smoke`는 외부 데이터 없이 주문·체결·포지션 경로 자체를 빠르게 확인하기 위해 공식 quickstart의 low-level `BacktestEngine` 사용 패턴을 최소화한 것입니다.
