#!/usr/bin/env python3
"""Single-period adapter for the existing v36 shared Nautilus runner."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess

from shared_workflow_adapter import _replace_period, _run_blocks


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument('--workflow',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--label',required=True)
    parser.add_argument('--warmup',required=True)
    parser.add_argument('--start',required=True)
    parser.add_argument('--end',required=True)
    args=parser.parse_args()
    blocks=_run_blocks(args.workflow)
    if not blocks:
        raise RuntimeError('no existing shared_account_backtest run block found')
    block=max(blocks,key=lambda value:value.count('shared_account_backtest'))
    script=_replace_period(block,warmup=args.warmup,start=args.start,end=args.end,root=args.output_root,label=args.label)
    args.output_root.mkdir(parents=True,exist_ok=True)
    path=args.output_root/f'{args.label}-command.sh'
    path.write_text('set -euo pipefail\n'+script+'\n',encoding='utf-8')
    env=os.environ.copy(); env.update({'ROOT':str(args.output_root/args.label),'CACHE':str(Path('.cache')/f'candidate-05-v47-{args.label}'),'PYTHONPATH':'research/candidate-05'})
    subprocess.run(['bash',str(path)],check=True,env=env)


if __name__=='__main__':
    main()
