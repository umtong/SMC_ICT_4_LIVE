"""Separate short accounts. A long account is never synthesized from these runs."""
import argparse,json,subprocess,sys
from pathlib import Path
WINDOWS=[('2024-08-03','2024-08-10'),('2025-08-10','2025-08-17'),('2025-11-17','2025-11-24'),('2025-02-10','2025-02-17'),('2026-04-13','2026-04-20'),('2026-07-13','2026-07-20')]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--start'); ap.add_argument('--end'); ap.add_argument('--execution-root'); ap.add_argument('--policy',choices=['response','early'],default='response'); args=ap.parse_args()
    out=Path('research_results/candidate_ml_easychart_astra2')/f'{args.policy}_native'
    if args.start:
        import nautilus_account as account
        if args.policy=='response': from retest_response import candidates
        else: from early_transfer import candidates
        account.candidates=candidates; account.OUT=out
        account.run(args.start,args.end,args.execution_root)
        return
    summaries=[]
    for start,end in WINDOWS:
        subprocess.run([sys.executable,__file__,'--policy',args.policy,'--start',start,'--end',end],check=True)
        result=json.loads((out/f'nautilus_transfer_{start}_1-MINUTE_summary.json').read_text())
        result['policy']=args.policy; summaries.append(result)
    (out/'short_results.json').write_text(json.dumps(summaries,indent=2)+'\n')
if __name__=='__main__': main()
