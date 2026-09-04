from pathlib import Path
p=Path(__file__).with_name('research.py');s=p.read_text()
old="OUT.mkdir(parents=True,exist_ok=True)"
new=old+"\nLOG_GUARDS=[]  # Keep Nautilus' process-global logger alive across engine disposal."
assert s.count(old)==1;s=s.replace(old,new)
old='    engine=make_engine(funding,liquidity,starting_nav)'
new=old+"\n    if engine.kernel._log_guard is not None:LOG_GUARDS.append(engine.kernel._log_guard)"
assert s.count(old)==1;s=s.replace(old,new)
old='def main():\n'
new=old+"    (OUT/'error.txt').unlink(missing_ok=True)\n"
assert s.count(old)==1;s=s.replace(old,new);p.write_text(s)
Path(__file__).unlink()
