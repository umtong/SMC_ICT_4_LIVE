"""Pure multiscale directional-change states for Candidate 05 v49."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(slots=True)
class DirectionalChangeState:
    multiple: float
    mode: int = 0
    extreme: float = math.nan
    last_change_index: int = -1
    last_change_side: int = 0
    last_extreme: float = math.nan

    def update(self,*,high:float,low:float,close:float,atr:float,index:int)->int:
        if not all(math.isfinite(value) for value in (high,low,close,atr)) or atr<=0.0:
            return 0
        threshold=self.multiple*atr
        if self.mode==0:
            self.mode=1
            self.extreme=high
            return 0
        if self.mode>0:
            self.extreme=max(self.extreme,high)
            if low<=self.extreme-threshold:
                self.last_extreme=self.extreme
                self.mode=-1
                self.extreme=low
                self.last_change_index=index
                self.last_change_side=-1
                return -1
            return 0
        self.extreme=min(self.extreme,low)
        if high>=self.extreme+threshold:
            self.last_extreme=self.extreme
            self.mode=1
            self.extreme=high
            self.last_change_index=index
            self.last_change_side=1
            return 1
        return 0


def aligned_change(first:DirectionalChangeState,second:DirectionalChangeState,*,side:int,max_delay:int=2)->bool:
    return (
        side in (-1,1)
        and first.last_change_side==side
        and second.last_change_side==side
        and abs(first.last_change_index-second.last_change_index)<=max_delay
    )


def trend_pullback_realignment(*,large_mode:int,small_previous_change:int,small_current_change:int)->int:
    if large_mode not in (-1,1):return 0
    if small_previous_change==-large_mode and small_current_change==large_mode:return large_mode
    return 0
