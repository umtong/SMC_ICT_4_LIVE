from __future__ import annotations

import json
import math
from dataclasses import replace

import pytest

from smc_ict_4.campaign_policy.latent_owner import OwnerDirection, OwnerIdentity
from smc_ict_4.campaign_policy.owner_observation import (
    CompletedMarketBar,
    DEFAULT_SYMBOLS,
    OwnerObservationBuilder,
    SourceGeometry,
)


FIVE_MINUTES_NS = 5 * 60 * 1_000_000_000
BASE = {
    "BTCUSDT": 100.0,
    "ETHUSDT": 50.0,
    "SOLUSDT": 20.0,
    "XRPUSDT": 1.0,
}


def _bars(
    step: int,
    returns: dict[str, float] | None = None,
    flows: dict[str, float] | None = None,
    *,
    optional: bool = False,
) -> dict[str, CompletedMarketBar]:
    returns = returns or {}
    flows = flows or {}
    result: dict[str, CompletedMarketBar] = {}
    for symbol in DEFAULT_SYMBOLS:
        prior = BASE[symbol] * (1.001 ** max(step - 1, 0))
        close = prior * (1.0 + returns.get(symbol, 0.001))
        signed = flows.get(symbol, 20.0)
        quote = max(100.0, abs(signed))
        buy = (quote + signed) / 2.0
        result[symbol] = CompletedMarketBar(
            symbol=symbol,
            open_time_ns=step * FIVE_MINUTES_NS,
            close_time_ns=(step + 1) * FIVE_MINUTES_NS,
            open=prior,
            high=max(prior, close) * 1.0005,
            low=min(prior, close) * 0.9995,
            close=close,
            quote_volume=quote,
            taker_buy_quote_volume=buy,
            spot_quote_volume=80.0 if optional else None,
            spot_taker_buy_quote_volume=45.0 if optional else None,
            open_interest=1_000.0 + step * 10.0 if optional else None,
            basis=0.001 + step * 0.0001 if optional else None,
            depth_imbalance=0.2 if optional else None,
        )
    return result


def _geometry(
    symbol: str = "BTCUSDT",
    direction: OwnerDirection = OwnerDirection.LONG,
    source_id: str = "day-low",
) -> SourceGeometry:
    reference = BASE[symbol]
    sign = 1.0 if direction is OwnerDirection.LONG else -1.0
    return SourceGeometry(
        identity=OwnerIdentity(f"{symbol}:{source_id}", 1, direction),
        symbol=symbol,
        direction=direction,
        source_lower=reference * 0.997,
        source_upper=reference * 1.003,
        target_price=reference * (1.0 + sign * 0.10),
        attack_reference_price=reference,
    )


def _seed(builder: OwnerObservationBuilder, count: int = 4) -> None:
    for step in range(count):
        builder.observe(_bars(step), ())


def test_prefix_invariance_when_a_future_suffix_is_added() -> None:
    left = OwnerObservationBuilder()
    right = OwnerObservationBuilder()
    _seed(left)
    _seed(right)
    geometry = _geometry()

    left_observation = left.observe(_bars(4, {"BTCUSDT": 0.004}), (geometry,))[geometry.identity]
    right_observation = right.observe(_bars(4, {"BTCUSDT": 0.004}), (geometry,))[geometry.identity]
    prefix_snapshot = left.canonical_snapshot()

    right.observe(_bars(5, {"BTCUSDT": -0.03}), (geometry,))

    assert left_observation == right_observation
    assert left.canonical_snapshot() == prefix_snapshot
    assert right.canonical_snapshot() != prefix_snapshot


def test_current_bar_is_not_used_to_choose_its_own_flow_scale() -> None:
    small = OwnerObservationBuilder()
    large = OwnerObservationBuilder()
    _seed(small)
    _seed(large)
    geometry = _geometry()

    small_value = small.observe(
        _bars(4, flows={"BTCUSDT": 40.0}), (geometry,)
    )[geometry.identity].perp_flow
    large_value = large.observe(
        _bars(4, flows={"BTCUSDT": 400.0}), (geometry,)
    )[geometry.identity].perp_flow

    assert small_value is not None and large_value is not None
    assert large_value == pytest.approx(10.0 * small_value)


def test_broad_market_return_is_common_nuisance_not_local_residual() -> None:
    builder = OwnerObservationBuilder()
    _seed(builder)
    geometries = tuple(_geometry(symbol, source_id="broad") for symbol in DEFAULT_SYMBOLS)

    observations = builder.observe(
        _bars(4, returns={symbol: 0.01 for symbol in DEFAULT_SYMBOLS}), geometries
    )

    assert all(abs(observation.residual_return or 0.0) < 1e-12 for observation in observations.values())
    assert all(observation.common_nuisance is not None for observation in observations.values())


def test_isolated_move_remains_positive_local_residual() -> None:
    builder = OwnerObservationBuilder()
    _seed(builder)
    geometry = _geometry()

    observation = builder.observe(
        _bars(
            4,
            returns={
                "BTCUSDT": 0.02,
                "ETHUSDT": 0.0,
                "SOLUSDT": 0.0,
                "XRPUSDT": 0.0,
            },
        ),
        (geometry,),
    )[geometry.identity]

    assert observation.residual_return is not None
    assert observation.residual_return > 0.0


def test_missing_optional_dimensions_stay_missing() -> None:
    builder = OwnerObservationBuilder()
    _seed(builder)
    geometry = _geometry()

    observation = builder.observe(_bars(4), (geometry,))[geometry.identity]

    assert observation.spot_flow is None
    assert observation.open_interest_change is None
    assert observation.basis_change is None
    assert observation.depth_imbalance is None


def test_long_and_short_views_have_symmetric_signs_except_anonymous_oi() -> None:
    builder = OwnerObservationBuilder()
    for step in range(4):
        builder.observe(_bars(step, optional=True), ())
    long_geometry = _geometry(direction=OwnerDirection.LONG, source_id="symmetric")
    short_geometry = _geometry(direction=OwnerDirection.SHORT, source_id="symmetric")

    observations = builder.observe(
        _bars(4, {"BTCUSDT": 0.006}, optional=True),
        (long_geometry, short_geometry),
    )
    long = observations[long_geometry.identity]
    short = observations[short_geometry.identity]

    for field in (
        "return_progress",
        "source_progress",
        "spot_flow",
        "perp_flow",
        "distance_from_source",
        "target_progress",
        "residual_return",
        "basis_change",
        "depth_imbalance",
    ):
        assert getattr(long, field) == pytest.approx(-getattr(short, field))
    assert long.common_nuisance == pytest.approx(short.common_nuisance)
    assert long.open_interest_change == short.open_interest_change


def test_zero_executed_flow_marginalizes_undefined_impact() -> None:
    builder = OwnerObservationBuilder()
    _seed(builder)
    geometry = _geometry()
    flows = {symbol: 0.0 for symbol in DEFAULT_SYMBOLS}

    observation = builder.observe(
        _bars(4, {"BTCUSDT": 0.01}, flows), (geometry,)
    )[geometry.identity]

    assert observation.impact_per_flow is None


def test_source_owner_evidence_is_invariant_to_selected_target_distance() -> None:
    near_builder = OwnerObservationBuilder()
    far_builder = OwnerObservationBuilder()
    _seed(near_builder)
    _seed(far_builder)
    near = _geometry()
    far = replace(near, target_price=near.attack_reference_price * 1.50)

    near_observation = near_builder.observe(
        _bars(4, {"BTCUSDT": 0.006}), (near,)
    )[near.identity]
    far_observation = far_builder.observe(
        _bars(4, {"BTCUSDT": 0.006}), (far,)
    )[far.identity]

    assert near_observation.source_progress == pytest.approx(far_observation.source_progress)
    assert near_observation.distance_from_source == pytest.approx(
        far_observation.distance_from_source
    )
    assert near_observation.available() == far_observation.available()


def test_pre_signal_geometry_updates_owner_evidence_without_a_target() -> None:
    builder = OwnerObservationBuilder()
    _seed(builder)
    geometry = replace(_geometry(), target_price=None)

    observation = builder.observe(
        _bars(4, {"BTCUSDT": 0.006}), (geometry,)
    )[geometry.identity]

    assert observation.source_progress is not None
    assert observation.distance_from_source is not None
    assert observation.perp_flow is not None
    assert observation.target_progress is None


def test_optional_level_returning_after_gap_is_not_mislabeled_one_bar_change() -> None:
    builder = OwnerObservationBuilder()
    geometry = _geometry()
    builder.observe(_bars(0, optional=True), ())
    builder.observe(_bars(1, optional=True), ())
    builder.observe(_bars(2, optional=False), ())

    observation = builder.observe(_bars(3, optional=True), (geometry,))[geometry.identity]

    assert observation.open_interest_change is None
    assert observation.basis_change is None


def test_same_global_bar_mapping_and_snapshot_restore_are_deterministic() -> None:
    left = OwnerObservationBuilder()
    geometries = (
        _geometry("ETHUSDT", OwnerDirection.SHORT, "high"),
        _geometry("BTCUSDT", OwnerDirection.LONG, "low"),
    )
    _seed(left)
    snapshot = json.loads(json.dumps(left.export_state()))
    right = OwnerObservationBuilder.from_state(snapshot)

    left_result = left.observe(_bars(4, optional=True), geometries)
    right_result = right.observe(_bars(4, optional=True), tuple(reversed(geometries)))

    assert list(left_result) == list(right_result)
    assert left_result == right_result
    assert left.canonical_snapshot() == right.canonical_snapshot()
