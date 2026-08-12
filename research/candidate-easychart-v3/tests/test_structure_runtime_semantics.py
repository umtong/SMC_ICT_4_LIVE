from __future__ import annotations

from types import SimpleNamespace
import unittest

import structure_runtime_v3  # noqa: F401
from contracts_v5 import Pivot
from domain import Candle, Side
from scenario_execution_v5 import ScenarioExecutionMixin
from structure_v5 import CausalStructureBook


class StructureRuntimeSemanticsTest(unittest.TestCase):
    @staticmethod
    def bar(ts: int, *, high: float, low: float) -> Candle:
        close = (high + low) / 2.0
        return Candle(ts_close_ns=ts, open=close, high=high, low=low, close=close, volume=1.0)

    def test_equal_high_touch_does_not_spend_liquidity(self) -> None:
        book = CausalStructureBook("TEST", 15, 0.1, pivot_spans=(2,))
        pivot = Pivot(
            pivot_id="high",
            side="HIGH",
            price=100.0,
            index=0,
            event_time_ns=1,
            observed_index=0,
            observed_time_ns=10,
            span=2,
            strength_˜][ÏLKŒˆ
Bˆ›ÛÚËœ]›ÝË˜\[™
]›Ý
Bˆ›ÛÚË—ØXÝ]™WÜ]›ÝÖÜ]›Ýœ]›ÝÚYHH]›Ýˆ›ÛÚË›ØœÙ\™WÜšXÙJÙ[‹˜˜\ŠŒYÚLLŒÝÏNNKŒ
JBˆÙ[‹˜\ÜÙ\\Ó›Ý›Û™J]›Ý™š\œÝÝÝXÚÝ[YWÛœÊBˆÙ[‹˜\ÜÙ\˜[ÙJ]›Ý˜ÛÛœÝ[YY
BˆÙ[‹˜\ÜÙ\[Š]›Ýœ]›ÝÚY›ÛÚË—ØXÝ]™WÜ]›ÝÊB‚ˆYˆ\ÝÛÛ™WÝXÚ×Ý˜YWØ™^[Û™ÚYÚÜÜ[™×Û\]ZY]JÙ[ŠHOˆ›Û™N‚ˆ›ÛÚÈHØ]\Ø[ÝXÝ\™P›ÛÚÊ•TÕ‹MKŒK]›ÝÜÜ[œÏJ‹
JBˆ]›ÝH]›Ý
ˆ]›ÝÚYHšYÚ‹ˆÚYOH’QÒ‹ˆšXÙOLLŒˆ[™^Lˆ]™[Ý[YWÛœÏLKˆØœÙ\™YÚ[™^LˆØœÙ\™YÝ[YWÛœÏLLˆÜ[L‹ˆÝ™[™ÝÇ&F–óÓãÀ¢¢&öö²ç—f÷G2æVæB‡—f÷B¢&öö²åö7F—fU÷—f÷G5·—f÷Bç—f÷Eö–EÒÒ—f÷@¢&öö²æö'6W'fU÷&–6R‡6VÆbæ&"ƒ#Â†–vƒÓãÂÆ÷sÓ“’ã’¢6VÆbæ76W'EG'VR‡—f÷Bæ6öç7VÖVB¢6VÆbæ76W'Dæ÷D–â‡—f÷Bç—f÷Eö–BÂ&öö²åö7F—fU÷—f÷G2 ¢FVbFW7Eö6†ææVÅö66WFæ6U÷W6W5÷&V'&Vµö÷&–v–åöæ÷EööæU÷F–6µöVFvR‡6VÆb’ÓâæöæS ¢÷&–v–âÒ6–×ÆTæÖW76R‡&–6SÓ“Rã¢6WGWÒ6–×ÆTæÖW76R‡6–FSÕ6–FRäÄôärÂ66WFæ6Uö÷&–v–ãÖ÷&–v–â¢Væv–æRÒ6–×ÆTæÖW76R‡F–6µ÷6—¦SÓã¢7F÷Ò66Væ&–ôW†V7WF–öäÖ—†–âåö66WFæ6U÷7F÷†Væv–æRÂ6WGWÂ#2¢6VÆbæ76W'DÆÖ÷7DWVÂ‡7F÷Â“Bã’  ¦–bõöæÖUõòÓÒ%õöÖ–åõò# ¢Væ—GFW7BæÖ–â‚ 