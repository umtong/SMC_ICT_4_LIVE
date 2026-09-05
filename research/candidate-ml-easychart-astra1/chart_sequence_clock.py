"""Archive routing for closed-bar chart sequences.

A bar stamped 00:00 on the first day belongs to the preceding month's archive.
Only archive selection subtracts one nanosecond; observation times and the
as-of restriction used by ChartStore are unchanged.
"""
import numpy as np
import pandas as pd
import chart_sequence_experiment as model


def sequences(rows):
    rows=rows.reset_index(drop=True)
    months=pd.to_datetime(rows.observed_time_ns-1,utc=True).dt.strftime('%Y-%m').to_numpy()
    result=np.empty((len(rows),8*len(model.SCALES)+1,model.LENGTH),dtype=np.float32)
    for month in np.unique(months):
        positions=np.flatnonzero(months==month)
        if month not in model.STORES:raise ValueError('observed chart archive not supplied: '+month)
        result[positions]=model.STORES[month].sequences(rows.iloc[positions].reset_index(drop=True))
    return result


def execute(base,request):
    model.sequences=sequences
    model.execute(base,request)
