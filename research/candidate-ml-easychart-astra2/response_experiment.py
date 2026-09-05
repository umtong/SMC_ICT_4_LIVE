"""One native account per separate diagnostic window; no stitched NAV claim."""
import argparse,json,subprocess,sys
from pathlib import Path
OUT=Path('research_results/candidate_ml_easychart_astra2/response_native')
WINDOWS=[('2024-08-03','2024-08-10'),('2025-08-10','2025-08-17'),('2025-11-17','2025-11-24'),('2025-02-10','2025-02-17'),('2026-04-13','2026-04-20'),('2026-07-13','2026-07-20')]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--start'); ap.add_argument('--end'); ap.add_argument('--execution-root'); args=ap.parse_args()
    if args.start:
        import nautilus_account as account
        from retest_response import candidates
        account.candidates=candidates; account.OUT=OUT
        account.run(args.start,args.end,args.execution_root)
        return
    summaries=[]
    for start,end in WINDOWS:
        subprocess.run([sys.executable,__file__,'--start',start,'--end',end],check=True)
        path=OUT/f'nautilus_transfer_{start}_1-MINUTE_summary.json'
        result=json.loads(path.read_text()); result['policy']='observed_origin_response'; summaries.append(result)
    (OUT/'short_results.json').write_text(json.dumps(summaries,indent=2)+'\n')
if __name__=='__main__': main()
