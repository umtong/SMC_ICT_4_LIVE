from __future__ import annotations
from easychart_re1_control_transfer import EasyChartRE1ControlTransferBundle
from easychart_re1_control_transfer_retest_core import DecisionFrameFirstRetestMixin
from easychart_re1_rejection_micro_target_v2 import FixedRejectionTargetMicroEngine, FixedRejectionTargetMajorSwingEngine, FixedRejectionTargetDecisionOBEngine, FixedRejectionTargetDirectSweepEngine

class CTMicro(DecisionFrameFirstRetestMixin, FixedRejectionTargetMicroEngine): pass
class CTMajor(DecisionFrameFirstRetestMixin, FixedRejectionTargetMajorSwingEngine): pass
class CTDecisionOB(DecisionFrameFirstRetestMixin, FixedRejectionTargetDecisionOBEngine): pass
class CTDirectSweep(DecisionFrameFirstRetestMixin, FixedRejectionTargetDirectSweepEngine): pass

class EasyChartRE1ControlTransferRetestBundle(EasyChartRE1ControlTransferBundle):
    def __init__(self, symbol: str, tick_size: float, minimum_gross_rr: float = 1.0) -> None:
        super().__init__(symbol, tick_size, minimum_gross_rr)
        common = dict(higher_minutes=15, decision_minutes=5, trigger_minutes=1, minimum_gross_rr=minimum_gross_rr)
        self.micro = CTMicro(symbol, tick_size, scale_name="MICRO", **common)
        self.major_swing = CTMajor(symbol, tick_size, scale_name="LIQUIDITY", **common)
        self.flow_decision_ob = CTDecisionOB(symbol, tick_size, scale_name="FLOW_DECISION_OB", **common)
        self.direct_sweep_ob = CTDirectSweep(symbol, tick_size, scale_name="DIRECT_SWEEP_OB", **common)
        for key in ("micro", "major_swing", "flow_decision_ob", "direct_sweep_ob"):
            self._audit_offsets[key] = 0

MultiScaleScenarioBundle = EasyChartRE1ControlTransferRetestBundle
