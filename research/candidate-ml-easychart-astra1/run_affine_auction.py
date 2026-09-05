"""Execute the affine auction hypothesis in the existing four-market account.

This is an experiment, not a paper-ready deployment. Each short interval is a
separate diagnostic; its NAV is never compounded with another interval's NAV.
"""
from pathlib import Path
import hashlib
import run_control_v2 as base
from affine_auction import AffineAuctionPolicy
from astra_policy import MINUTE

base.ControlPolicy = AffineAuctionPolicy
base.OUT = Path('research_results/astra1_affine_auction')
base.OUT.mkdir(parents=True, exist_ok=True)
source = Path(__file__).with_name('affine_auction.py').read_bytes()
base.CACHE = Path('astra_control_cache') / ('affine-' + hashlib.sha256(source).hexdigest()[:20])
base.CACHE.mkdir(parents=True, exist_ok=True)


class SourceClockStrategy(base.AccountStrategy):
    def score(self, plan):
        # Explicit ranking is required: the raw base score is a constant.
        return plan.features['rank']

    def on_bar(self, bar):
        plan = self.active_plan
        if (plan is not None and bar.bar_type.instrument_id == self.active_instrument_id
                and not self._portfolio_flat() and not self.emergency_exit_requested):
            timestamp = int(bar.ts_event)
            features = plan.features
            timeframe = int(features['close_failure_clock'])
            if timestamp // MINUTE % timeframe == 0:
                main = features['close_failure_origin_price'] + features['close_failure_slope'] * (
                    timestamp - int(features['close_failure_origin_time']))
                opposite = main + features['close_failure_offset']
                lower, upper = min(main, opposite), max(main, opposite)
                close = float(bar.close)
                failed = close < lower if int(plan.side.value) > 0 else close > upper
                if failed:
                    # A full exit on observed thesis failure, not an added daily
                    # risk gate. Mark sibling cancellations as intentional before
                    # using Nautilus' actual-position close command.
                    self.emergency_exit_requested = True
                    for order_id in (self.active_stop_id, self.active_target_id):
                        if order_id is not None:
                            self.expected_cancel_ids.add(order_id)
                    self._record('source_clock_channel_failure_full_exit',
                                 plan_id=plan.plan_id, source_timeframe=timeframe,
                                 observed_close=close, boundary_lower=lower,
                                 boundary_upper=upper)
                    self.cancel_all_orders(self.active_instrument_id)
                    self.close_all_positions(self.active_instrument_id)
        super().on_bar(bar)


base.AccountStrategy = SourceClockStrategy


def main():
    jobs = [('2024-03', '2024-03-10', '2024-03-17'),
            ('2024-08', '2024-08-24', '2024-08-31'),
            ('2025-02', '2025-02-10', '2025-02-17')]
    base.prepare([month for month, _, _ in jobs])
    results = []
    for month, start, end in jobs:
        tape = base.Tape(month)
        plans, stats, skipped = tape.plans()
        base.write(base.OUT / f'{month}_contexts.json', {'stats': stats, 'skipped': skipped})
        base.write(base.OUT / f'{month}_plans.json', [plan.record() for plan in plans])
        result = base.backtest(tape, plans, None, 'affine_' + month, start, end)
        results.append(result)
        base.write(base.OUT / 'latest.json', results)


if __name__ == '__main__':
    main()
