from __future__ import annotations

from futures_metrics_data import (
    FIVE_MINUTE_NS,
    ONE_MINUTE_NS,
    _causal_metric_timestamp,
)


def main() -> None:
    boundary = 1_713_753_000_000_000_000
    nominal, observable, offset = _causal_metric_timestamp(boundary)
    assert (nominal, observable, offset) == (boundary, boundary, 0)

    plus_one_second = boundary + 1_000_000_000
    nominal, observable, offset = _causal_metric_timestamp(plus_one_second)
    assert nominal == boundary
    assert observable == boundary + ONE_MINUTE_NS
    assert offset == 1_000_000_000
    assert observable >= plus_one_second

    minus_one_second = boundary - 1_000_000_000
    nominal, observable, offset = _causal_metric_timestamp(minus_one_second)
    assert nominal == boundary
    assert observable == boundary
    assert offset == -1_000_000_000
    assert observable >= minus_one_second

    plus_two_seconds = boundary + 2_000_000_000
    nominal, observable, offset = _causal_metric_timestamp(plus_two_seconds)
    assert nominal == boundary
    assert observable == boundary + ONE_MINUTE_NS
    assert offset == 2_000_000_000
    assert observable >= plus_two_seconds

    late_but_unambiguous = boundary + (FIVE_MINUTE_NS // 2) - 1
    nominal, observable, offset = _causal_metric_timestamp(late_but_unambiguous)
    assert nominal == boundary
    assert observable >= late_but_unambiguous
    assert offset == (FIVE_MINUTE_NS // 2) - 1

    try:
        _causal_metric_timestamp(boundary + FIVE_MINUTE_NS // 2)
    except ValueError as exc:
        assert "ambiguous" in str(exc)
    else:
        raise AssertionError("half-slot timestamp must fail as ambiguous")

    nominal_slots = [
        _causal_metric_timestamp(boundary + FIVE_MINUTE_NS * index + (1_000_000_000 if index == 1 else 0))[0]
        for index in range(3)
    ]
    assert nominal_slots == [boundary, boundary + FIVE_MINUTE_NS, boundary + 2 * FIVE_MINUTE_NS]
    print("causal futures metrics timestamp contract passed")


if __name__ == "__main__":
    main()
