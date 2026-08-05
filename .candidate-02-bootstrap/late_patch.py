"""Late source patch applied while candidate-02 is validated.

The helper is temporary and is removed after the permanent source/workflow is
committed. Every mutation is deterministic and idempotent.
"""

from pathlib import Path


backtest = Path("research/candidate-02/backtest.py")
text = backtest.read_text(encoding="utf-8")
text = text.replace(
    '"stats_general": _json_safe(result_obj.stats_general),',
    '"stats_general": _json_safe(getattr(result_obj, "stats_general", {})),',
)
if "import multiprocessing as mp\n" not in text:
    text = text.replace("import math\n", "import math\nimport multiprocessing as mp\n", 1)
helper = '''

def _run_window_in_isolated_process(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one window in a fresh process because NT logging is process-global."""

    return run_window(**payload)
'''
marker = "\ndef run_screen(\n"
if "def _run_window_in_isolated_process" not in text:
    text = text.replace(marker, helper + marker, 1)
old_loop = '''    for item in selection["locked_windows"]:
        start = _utc_midnight(item["start"])
        end = start + timedelta(days=7)
        result = run_window(
            label=item["role"],
            symbols=("BTCUSDT",),
            evaluation_start=start,
            evaluation_end=end,
            config=config,
            output=output / item["role"],
            cache_root=cache_root,
        )
        results.append(result["metrics"])
        all_events.extend(result["events"])
        all_data_files.extend(result["data_files"])
'''
new_loop = '''    spawn = mp.get_context("spawn")
    for item in selection["locked_windows"]:
        start = _utc_midnight(item["start"])
        end = start + timedelta(days=7)
        payload = {
            "label": item["role"],
            "symbols": ("BTCUSDT",),
            "evaluation_start": start,
            "evaluation_end": end,
            "config": config,
            "output": output / item["role"],
            "cache_root": cache_root,
        }
        # NautilusTrader 1.230.0 owns a process-global Rust logger. A fresh
        # child per window also guarantees clean engine/account/cache state.
        with spawn.Pool(processes=1, maxtasksperchild=1) as pool:
            result = pool.apply(_run_window_in_isolated_process, (payload,))
        results.append(result["metrics"])
        all_events.extend(result["events"])
        all_data_files.extend(result["data_files"])
'''
if old_loop in text:
    text = text.replace(old_loop, new_loop, 1)
elif new_loop not in text:
    raise RuntimeError("run_screen loop shape changed unexpectedly")
backtest.write_text(text, encoding="utf-8")
