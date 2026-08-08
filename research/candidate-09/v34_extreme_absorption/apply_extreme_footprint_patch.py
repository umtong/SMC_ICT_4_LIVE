#!/usr/bin/env python3
"""Add extreme price-level aggression metrics to the reused v33 footprint loader."""
from pathlib import Path
import sys


path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
if "top_extreme_aggressor_delta" in text:
    raise SystemExit(0)

old_compute = '''        total_notional = float(total.sum())
        poc_index = int(np.argmax(total)) if total.size else 0
        records.append(
'''
new_compute = '''        total_notional = float(total.sum())
        poc_index = int(np.argmax(total)) if total.size else 0
        extreme_count = min(5, int(total.size))
        bottom_buy = float(buy[:extreme_count].sum()) if extreme_count else 0.0
        bottom_sell = float(sell[:extreme_count].sum()) if extreme_count else 0.0
        top_buy = float(buy[-extreme_count:].sum()) if extreme_count else 0.0
        top_sell = float(sell[-extreme_count:].sum()) if extreme_count else 0.0
        bottom_total = bottom_buy + bottom_sell
        top_total = top_buy + top_sell
        bottom_extreme_delta = (
            (bottom_buy - bottom_sell) / bottom_total if bottom_total > 0.0 else 0.0
        )
        top_extreme_delta = (
            (top_buy - top_sell) / top_total if top_total > 0.0 else 0.0
        )
        bottom_cell_multiple = (
            float(total[:extreme_count].max()) / max(median_cell, 1.0)
            if extreme_count
            else 0.0
        )
        top_cell_multiple = (
            float(total[-extreme_count:].max()) / max(median_cell, 1.0)
            if extreme_count
            else 0.0
        )
        records.append(
'''
if old_compute not in text:
    raise RuntimeError("v33 footprint computation insertion point not found")
text = text.replace(old_compute, new_compute, 1)

old_record = '''                "footprint_cell_median_notional": median_cell,
            }
'''
new_record = '''                "footprint_cell_median_notional": median_cell,
                "top_extreme_aggressor_delta": top_extreme_delta,
                "bottom_extreme_aggressor_delta": bottom_extreme_delta,
                "top_extreme_notional_share": (
                    top_total / total_notional if total_notional > 0.0 else 0.0
                ),
                "bottom_extreme_notional_share": (
                    bottom_total / total_notional if total_notional > 0.0 else 0.0
                ),
                "top_extreme_cell_multiple": top_cell_multiple,
                "bottom_extreme_cell_multiple": bottom_cell_multiple,
            }
'''
if old_record not in text:
    raise RuntimeError("v33 footprint record insertion point not found")
text = text.replace(old_record, new_record, 1)

old_columns = '''        "footprint_cell_median_notional",
    ]
'''
new_columns = '''        "footprint_cell_median_notional",
        "top_extreme_aggressor_delta",
        "bottom_extreme_aggressor_delta",
        "top_extreme_notional_share",
        "bottom_extreme_notional_share",
        "top_extreme_cell_multiple",
        "bottom_extreme_cell_multiple",
    ]
'''
if old_columns not in text:
    raise RuntimeError("v33 footprint column insertion point not found")
text = text.replace(old_columns, new_columns, 1)
path.write_text(text, encoding="utf-8")
