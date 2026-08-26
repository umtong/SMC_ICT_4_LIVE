"""Use the existing responsible flow-OB policy with a slower four-hour context auction.

The source trading cases separate broad/medium direction from 15m/5m/1m execution.
This ablation changes only the top router horizon from 60 minutes to 240 minutes;
all lower scenario, entry, stop, target, execution and risk rules remain unchanged.
"""
from easychart_re1_flow_ob_responsibility import EasyChartRE1ResponsibleFlowOBBundle


class EasyChartRE1ResponsibleFlowOB4HBundle(EasyChartRE1ResponsibleFlowOBBundle):
    CONTEXT_MINUTES = 240


MultiScaleScenarioBundle = EasyChartRE1ResponsibleFlowOB4HBundle
