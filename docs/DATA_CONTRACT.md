# 데이터 계약

## 시간

모든 시각은 UTC 기반 Unix nanoseconds 정수입니다.

- `event_time_ns`: 시장 사건이 귀속되는 시각
- `observed_time_ns`: 알고리즘이 그 사건을 확정적으로 알 수 있게 된 시각

항상 다음 관계를 만족해야 합니다.

```text
observed_time_ns >= event_time_ns
```

미래 바를 확인해야 확정되는 swing처럼 두 시각이 다른 사건은 반드시 둘을 구분합니다.

## 시장 데이터

각 데이터 세트는 다음 metadata를 가져야 합니다.

- venue와 instrument identifier
- 데이터 유형(quote, trade, bar, order-book 등)
- 시작·종료 시각
- 원본 공급자와 변환 코드 버전
- 파일별 byte size와 SHA-256
- 결측·중복 검사 결과

큰 데이터 파일은 Git에 커밋하지 않습니다. `smc4 data-manifest`로 생성한 작은 JSON manifest만 연구 실행과 연결합니다.

## NautilusTrader 원칙

instrument 정의는 가격·수량 정밀도, increment, 통화, 계약 의미를 제공합니다. 현재 거래소 사양을 전략 코드에 임의로 하드코딩하지 말고 데이터 시점에 맞는 instrument metadata를 카탈로그에 저장합니다.
