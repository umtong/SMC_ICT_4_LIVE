"""Small entry point for the explicitly selected research experiment."""
from pathlib import Path
import importlib
import json
import traceback

HERE=Path(__file__).resolve().parent
request=json.loads((HERE/'request.json').read_text())
module={'role_frontier':'frontier_experiment'}.get(request.get('driver'),'path_research')
if __name__=='__main__':
    try:
        importlib.import_module(module).run()
    except Exception:
        output=Path('research_results/candidate_ml_easychart_astra3')
        output.mkdir(parents=True,exist_ok=True)
        (output/'error.txt').write_text(traceback.format_exc())
        raise
