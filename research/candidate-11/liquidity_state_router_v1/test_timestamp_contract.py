from __future__ import annotations
import pandas as pd


def test_parquet_millisecond_datetime_is_normalized_to_nanoseconds() -> None:
    values = pd.Series(pd.to_datetime(["2023-11-20T00:00:00Z", "2023-11-20T00:01:00Z"]))
    values = values.astype("datetime64[ms, UTC]")
    actual = values.astype("datetime64[ns, UTC]").astype("int64").tolist()
    assert actual == [1700438400000000000, 1700438460000000000]


def test_old_integer_conversion_is_not_accepted_as_nanoseconds() -> None:
    values = pd.Series(pd.to_datetime(["2023-11-20T00:00:00Z"]).astype("datetime64[ms, UTC]"))
    old = int(values.astype("int64").iloc[0])
    new = int(values.astype("datetime64[ns, UTC]").astype("int64").iloc[0])
    assert old == 1700438400000
    assert new == 1700438400000000000
    assert new == old * 1_000_000
