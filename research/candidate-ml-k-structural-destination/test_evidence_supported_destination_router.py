from __future__ import annotations

import math
import numpy as np
import pandas as pd

import evidence_supported_destination_router as router


def test_feature_contract_excludes_symbol_prices_future_and_common_uplift() -> None:
    frame = pd.DataFrame({
        'symbol':['BTCUSDT'], 'entry':[100000.0], 'stop':[99000.0], 'target':[102000.0],
        'outcome':['TARGET_FIRST'], 'mfe_r':[4.0], 'future_path':[9.0],
        'event_residual_return_5m_signed':[0.5], 'event_common_return_5m_signed':[2.0],
        'control_move_atr':[1.2], 'gross_rr':[2.0], 'planned_target_net_r':[1.7],
        'family':['FAILED_AUCTION_REVERSAL'], 'setup_kind':['IFVG'],
        'entry_geometry':['OB_FVG_OVERLAP'],
    })
    features = router.causal_features(frame, include_common=False, include_target=True)
    forbidden = {'symbol','entry','stop','target','outcome','mfe_r','future_path','event_common_return_5m_signed'}
    assert forbidden.isdisjoint(features.columns)
    assert 'event_residual_return_5m_signed' in features.columns
    assert 'gross_rr' in features.columns


def test_entropic_destination_utility_resists_remote_tail_model_error() -> None:
    # Both targets have the same arithmetic mean utility.  The far destination is
    # supported only in one historical regime, so its certainty equivalent is worse.
    near = np.array([[0.010, 0.010, 0.010]]).T
    far = np.array([[-0.020, -0.020, 0.060]]).T
    near_ce = float(router.entropic_certainty_equivalent(near.T)[0])
    far_ce = float(router.entropic_certainty_equivalent(far.T)[0])
    assert near_ce > 0.0
    assert far_ce < near_ce


def test_target_choice_is_per_decision_not_future_episode() -> None:
    scored = pd.DataFrame({
        'period':['fresh']*4,
        'episode_id':['episode']*4,
        'state_id':['state-1','state-1','state-2','state-2'],
        'order_time_ns':[1,1,2,2],
        'action_id':['near-1','far-1','near-2','far-2'],
        'models_ready':[True]*4,
        'robust_log_growth_per_hour':[0.02,0.01,0.005,0.03],
        'robust_expected_log_growth':[0.01,0.02,0.004,0.015],
        'p_target_worst_regime':[0.6,0.4,0.7,0.5],
        'planned_target_net_r':[1.2,2.4,1.1,2.0],
        'gross_rr':[1.4,2.8,1.3,2.5],
    })
    prepared = router.prepare_labels(scored.assign(
        fill_time_ns=np.nan, order_terminal_time_ns=10, resolution_time_ns=np.nan,
        outcome='UNFILLED', role='fresh'
    ))
    prepared['models_ready']=True
    prepared['robust_log_growth_per_hour']=scored.robust_log_growth_per_hour.to_numpy()
    prepared['robust_expected_log_growth']=scored.robust_expected_log_growth.to_numpy()
    prepared['p_target_worst_regime']=scored.p_target_worst_regime.to_numpy()
    selected = router.best_destination_per_decision(prepared)
    assert selected.action_id.tolist() == ['near-1','near-2']


def test_monotone_support_curve_makes_farther_target_no_easier() -> None:
    rows=[]
    for i,(rr,y) in enumerate([(1.0,1),(1.2,1),(1.5,0),(2.0,0),(3.0,0)]*20):
        rows.append({'period':'dev-a','state_id':f's{i}','episode_id':f'e{i}',
                     'action_id':f'a{i}','gross_rr':rr,'target_label':y,
                     'resolved_label':True,'decision_weight':1.0,
                     'family':'FAILED_AUCTION_REVERSAL','setup_kind':'IFVG',
                     'route_scale_bucket':'LOCAL'})
    train=pd.DataFrame(rows)
    curve=router.fit_support_curves(train)['dev-a']
    test=pd.DataFrame({'gross_rr':[1.0,1.5,3.0],
                       'family':['FAILED_AUCTION_REVERSAL']*3,
                       'setup_kind':['IFVG']*3,
                       'route_scale_bucket':['LOCAL']*3})
    probability=curve.predict(test)
    assert np.all(np.diff(probability) <= 1e-12)


def test_three_percent_risk_sizing_is_unchanged() -> None:
    sizing=router.risk_sized_quantity(nav=10000.0,entry=100.0,stop=99.5,quantity_step=0.001)
    assert math.isclose(sizing['risk_fraction'],0.03,abs_tol=1e-10)
    assert math.isclose(sizing['implied_leverage'],6.0,abs_tol=1e-10)
