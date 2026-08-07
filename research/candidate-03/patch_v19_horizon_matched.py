#!/usr/bin/env python3
"""Make V19 observation thresholds horizon-matched and add detector warm-up data."""
from __future__ import annotations

import hashlib
import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "derive_nt_lvcfr_v19_signals.py"
TESTS = ROOT / "test_nt_lvcfr_v19.py"
PREPARE = ROOT / "prepare_nt_lvcfr_v19.py"
RUNNER = ROOT / "run_v19_staged_container.py"


def git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    return hashlib.sha1(
        f"blob {len(payload)}\0".encode() + payload,
        usedforsecurity=False,
    ).hexdigest()


def patch_source() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    if "def rolling_horizon_thresholds(" not in source:
        helper = textwrap.dedent(
            '''

            def rolling_horizon_thresholds(
                blocks: Sequence[TradeBlock],
                *,
                direction: int,
                baseline_median_gross: float,
            ) -> dict[int, dict[str, float]]:
                """Build causal thresholds from pre-event windows of equal length."""
                thresholds: dict[int, dict[str, float]] = {}
                for width in range(2, OBSERVATION_BLOCKS + 1):
                    features = [
                        feature
                        for start in range(0, len(blocks) - width + 1)
                        if (
                            feature := cumulative_features(
                                blocks[start : start + width],
                                direction=direction,
                                baseline_median_gross=baseline_median_gross,
                            )
                        )
                        is not None
                    ]
                    available = len(blocks) - width + 1
                    minimum = max(10, math.ceil(available / 2))
                    if len(features) < minimum:
                        continue
                    thresholds[width] = {
                        "futures_response_q25": quantile(
                            [item.response_score for item in features],
                            0.25,
                        ),
                        "futures_response_q75": quantile(
                            [item.response_score for item in features],
                            0.75,
                        ),
                        "futures_flow_q75": quantile(
                            [item.directional_flow for item in features],
                            0.75,
                        ),
                        "futures_efficiency_q50": quantile(
                            [item.path_efficiency for item in features],
                            0.50,
                        ),
                        "baseline_windows": float(len(features)),
                    }
                return thresholds
            '''
        )
        marker = "\n\ndef collect_contexts("
        if marker not in source:
            raise RuntimeError("collect_contexts insertion marker missing")
        source = source.replace(marker, helper + marker, 1)

    source = source.replace(
        '        "INSUFFICIENT_CONTEXT": 0,\n',
        '        "INSUFFICIENT_CONTEXT": 0,\n'
        '        "INSUFFICIENT_HORIZON_BASELINE": 0,\n',
        1,
    )

    old_thresholds = re.compile(
        r"        futures_baseline_features = \[.*?"
        r"        thresholds = \{\n.*?"
        r"        \}\n",
        re.DOTALL,
    )
    replacement = textwrap.dedent(
        '''
                horizon_thresholds = rolling_horizon_thresholds(
                    futures_baseline_blocks,
                    direction=candidate.direction,
                    baseline_median_gross=futures_median_gross,
                )
                if not horizon_thresholds:
                    routing_counts["INSUFFICIENT_HORIZON_BASELINE"] += 1
                    continue
                threshold_rows.append(
                    {
                        "candidate": candidate.scenario_id,
                        "inventory_regime": candidate.inventory_regime,
                        "thresholds_by_observation_blocks": {
                            str(width): values
                            for width, values in horizon_thresholds.items()
                        },
                    }
                )
                thresholds = horizon_thresholds[max(horizon_thresholds)]
        '''
    )
    if "futures_baseline_features = [" in source:
        source, count = old_thresholds.subn(replacement, source, count=1)
        if count != 1:
            raise RuntimeError("one-block threshold section not found")

    old_observation = textwrap.dedent(
        '''
                    if future_features is None or spot_features is None:
                        continue
                    high_response = (
        '''
    )
    new_observation = textwrap.dedent(
        '''
                    if future_features is None or spot_features is None:
                        continue
                    current_thresholds = horizon_thresholds.get(count)
                    if current_thresholds is None:
                        continue
                    thresholds = current_thresholds
                    high_response = (
        '''
    )
    if old_observation in source:
        source = source.replace(old_observation, new_observation, 1)
    elif "current_thresholds = horizon_thresholds.get(count)" not in source:
        raise RuntimeError("observation threshold insertion marker missing")

    bottom_diagnostics = re.compile(
        r"        threshold_rows\.append\(\n"
        r"            \{\n"
        r"                \"candidate\": candidate\.scenario_id,\n"
        r"                \"inventory_regime\": candidate\.inventory_regime,\n"
        r"                \"thresholds\": thresholds,\n"
        r"            \}\n"
        r"        \)\n"
    )
    source = bottom_diagnostics.sub("", source)

    source = source.replace(
        '            "TEN_MINUTE_EVENT_EXCLUDED_BASELINE",\n'
        '            "SEQUENTIAL_SIXTY_SECOND_RESPONSE",\n',
        '            "TEN_MINUTE_PRE_EVENT_BASELINE",\n'
        '            "TEN_MINUTE_INVENTORY_EVENT_EXCLUDED",\n'
        '            "HORIZON_MATCHED_SEQUENTIAL_SIXTY_SECOND_RESPONSE",\n',
        1,
    )
    source = source.replace(
        '        "threshold_policy": "candidate-local causal quartiles, three coherent evidence axes, no return-fit search",\n',
        '        "threshold_policy": (\n'
        '            "candidate-local horizon-matched rolling causal quartiles, "\n'
        '            "three coherent evidence axes, no return-fit search"\n'
        '        ),\n'
        '        "baseline_relation": "TEN_MINUTES_ENDING_AT_EVENT_START",\n',
        1,
    )

    compile(source, str(SOURCE), "exec")
    SOURCE.write_text(source, encoding="utf-8")


def patch_tests() -> None:
    tests = TESTS.read_text(encoding="utf-8")
    if "rolling_horizon_thresholds," not in tests:
        tests = tests.replace(
            "    cumulative_features,\n",
            "    cumulative_features,\n    rolling_horizon_thresholds,\n",
            1,
        )
    if "test_horizon_thresholds_use_equal_length_pre_event_windows" not in tests:
        marker = "    def test_project_risk_and_native_execution_contract_are_fixed"
        new_test = textwrap.indent(
            textwrap.dedent(
                '''
                def test_horizon_thresholds_use_equal_length_pre_event_windows(self) -> None:
                    blocks = []
                    price = 100.0
                    for index in range(BASELINE_BLOCKS):
                        block = TradeBlock()
                        block.add(price, 1.0, False)
                        price *= 1.0001 if index % 2 == 0 else 0.99995
                        block.add(price, 1.0, index % 3 == 0)
                        blocks.append(block)
                    median_gross = sorted(
                        block.gross_notional for block in blocks
                    )[len(blocks) // 2]
                    thresholds = rolling_horizon_thresholds(
                        blocks,
                        direction=1,
                        baseline_median_gross=median_gross,
                    )
                    self.assertEqual(set(thresholds), set(range(2, 7)))
                    self.assertGreaterEqual(
                        thresholds[6]["baseline_windows"],
                        10.0,
                    )
                    source = Path(__file__).with_name(
                        "derive_nt_lvcfr_v19_signals.py"
                    ).read_text(encoding="utf-8")
                    self.assertIn(
                        "current_thresholds = horizon_thresholds.get(count)",
                        source,
                    )
                    self.assertNotIn("futures_baseline_features = [", source)

                '''
            ),
            "    ",
        )
        if marker not in tests:
            raise RuntimeError("test insertion marker missing")
        tests = tests.replace(marker, new_test + marker, 1)
    tests = tests.replace(
        '        self.assertIn("spot/daily/aggTrades", preparation)\n',
        '        self.assertIn("spot/daily/aggTrades", preparation)\n'
        '        self.assertIn("args.week_start - timedelta(days=1)", preparation)\n',
        1,
    )
    compile(tests, str(TESTS), "exec")
    TESTS.write_text(tests, encoding="utf-8")


def patch_preparation() -> None:
    preparation = PREPARE.read_text(encoding="utf-8")
    preparation = preparation.replace(
        "    for day in daily_dates(args.week_start, args.week_start + timedelta(days=7)):\n",
        "    for day in daily_dates(\n"
        "        args.week_start - timedelta(days=1),\n"
        "        args.week_start + timedelta(days=7),\n"
        "    ):\n",
        1,
    )
    preparation = preparation.replace(
        '            "historical_contract_identical_across_frozen_weeks": True,\n',
        '            "historical_contract_identical_across_frozen_weeks": True,\n'
        '            "detector_warmup_days": 1,\n',
        1,
    )
    compile(preparation, str(PREPARE), "exec")
    PREPARE.write_text(preparation, encoding="utf-8")


def refreeze_runner() -> None:
    runner = RUNNER.read_text(encoding="utf-8")
    for path in (SOURCE, TESTS, PREPARE):
        relative = path.relative_to(ROOT.parent.parent).as_posix()
        blob = git_blob_sha(path)
        pattern = rf'("{re.escape(relative)}": )"[0-9a-f]{{40}}"'
        runner, count = re.subn(pattern, rf'\1"{blob}"', runner, count=1)
        if count != 1:
            raise RuntimeError(f"unable to refreeze {relative}")
        print(f"{relative} {blob}")
    RUNNER.write_text(runner, encoding="utf-8")


def main() -> int:
    patch_source()
    patch_tests()
    patch_preparation()
    refreeze_runner()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
