"""One native account per explicitly separate short research window."""
import json
from pathlib import Path
import nautilus_account as account
from retest_response import candidates
WINDOWS=[('2024-08-03','2024-08-10'),('2025-08-10','2025-08-17'),('2025-11-17','2025-11-24'),('2025-02-10','2025-02-17'),('2026-04-13','2026-04-20'),('2026-07-13','2026-07-20')]

def main():
    account.candidates=candidates
    account.OUT=Path('research_results/candidate_ml_easychart_astra2/response_native')
    summaries=[]
    for start,end in WINDOWS:
        result=account.run(start,end)
        result['policy']='observed_origin_response'
        summaries.append(result)
    (account.OUT/'short_results.json').write_text(json.dumps(summaries,indent=2)+'\n')
if __name__=='__main__': main()
