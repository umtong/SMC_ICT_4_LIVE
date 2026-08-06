# Candidate-06 research basis

이 후보는 기존 프로젝트 후보의 전략 코드를 읽지 않고, 공통 인프라와 외부의 시장미시구조·실행 문헌을 바탕으로 설계했다.

## 채택한 관찰

1. Cont, Kukanov, Stoikov, **The Price Impact of Order Book Events** (Journal of Financial Econometrics, 2014): 짧은 구간 가격 변화는 거래량 자체보다 best-level 공급·수요 변화인 order-flow imbalance와 깊이의 관계로 설명되는 부분이 크다. 본 후보는 full book OFI가 없는 공개 kline에서 taker-buy 비중을 제한된 aggressor-flow proxy로만 사용한다.
2. Large, **Measuring the resiliency of an electronic limit order book** (Journal of Financial Markets, 2007): 충격 이후 유동성과 가격이 회복되는 속도는 충격 자체와 구분되는 상태 변수다. LRB는 sweep 이후 reclaim/hold와 반대 displacement/retest를 분리한다.
3. Osler, **Stop-Loss Orders and Price Cascades in Currency Markets**: stop 군집 통과는 자기강화적 이동을 만들 수 있으므로 sweep 뒤 무조건 반전만 가정하지 않고 acceptance continuation을 별도 시나리오로 둔다.
4. Binance `binance-public-data`: USDT-M futures 1-minute archive, taker-buy base volume, 거래 수와 SHA-256 checksum을 공식 공개한다. 후보 데이터 loader는 archive와 `.CHECKSUM`을 함께 보존한다.
5. NautilusTrader v1.230.0 문서와 소스: bar 완성시각, adaptive OHLC ordering, contingent bracket, fill model, portfolio/accounting 계약을 그대로 사용한다.

## 구현하지 않은 과장

- kline taker-buy 비중을 실제 L2 OFI 또는 depth resiliency라고 부르지 않는다.
- 관측할 수 없는 liquidation, open interest, resting-order cancellation을 추정값으로 꾸며내지 않는다.
- SMC/ICT 용어를 개별 캔들 이름으로 환원하지 않는다. sweep는 사건 시작이며 진입은 후속 상태 순서가 확정된 뒤에만 가능하다.

## 참고 링크

- https://academic.oup.com/jfec/article/12/1/47/816163
- https://www.sciencedirect.com/science/article/abs/pii/S1386418106000528
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=920687
- https://github.com/binance/binance-public-data
- https://nautilustrader.io/docs/latest/concepts/backtesting/
- https://nautilustrader.io/docs/latest/concepts/orders/advanced/
