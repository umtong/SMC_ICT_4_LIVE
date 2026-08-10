# Candidate 05 v52/v53 paired causal anatomy

This is a mechanism diagnosis, not a binary strategy gate. Separate account returns are not summed into a claimed continuous account result.

Mechanism status: `{"h52": "PROVISIONALLY_SUPPORTED_IN_DIAGNOSTIC_EPISODES", "h53": "UNRESOLVED_CATCHUP_STATE_NOT_REACHED", "implementation": "VALID"}`

## week-2023-07

- **v52** — IMPLEMENTATION_VALID; trades=0, return=0.0, PF=0.0, reachability=FLOW_DEPTH_STATE_EXPLAINS_REJECTION
  - funnel: `{"v52_extremes": 1402, "v52_flow_depth_pass": 0, "v52_inflections": 29, "v52_oi_contraction_pass": 13, "v52_peer_context_ready": 40320, "v52_same_timestamp_peer_uses": 0, "v52_setups": 0, "v53_catchup_context": 0, "v53_catchup_flow_pass": 0, "v53_catchup_oi_pass": 0, "v53_catchup_setups": 0}`
  - mechanisms: `{}`
- **v53** — IMPLEMENTATION_VALID; trades=0, return=0.0, PF=0.0, reachability=FLOW_DEPTH_STATE_EXPLAINS_REJECTION
  - funnel: `{"v52_extremes": 1402, "v52_flow_depth_pass": 0, "v52_inflections": 29, "v52_oi_contraction_pass": 13, "v52_peer_context_ready": 40320, "v52_same_timestamp_peer_uses": 0, "v52_setups": 0, "v53_catchup_context": 16, "v53_catchup_flow_pass": 0, "v53_catchup_oi_pass": 12, "v53_catchup_setups": 0}`
  - mechanisms: `{}`

Paired comparison: `{"causal_comparison": "INTERPRETABLE", "hypothesis_attribution": "CATCHUP_STATE_NOT_EXECUTED", "net_pnl_delta_v53_minus_v52": 0.0, "residual_episode_preservation": {"matched_residual_episodes": 0, "matches": [], "v52_episode_preservation_fraction": null, "v52_residual_trades": 0, "v53_residual_trades": 0}, "total_return_delta_v53_minus_v52": 0.0, "trade_delta_v53_minus_v52": 0, "v52_residual_mechanism": {}, "v53_catchup_mechanism": {}, "v53_residual_mechanism": {}}`

## week-2023-09

- **v52** — IMPLEMENTATION_VALID; trades=0, return=0.0, PF=0.0, reachability=FLOW_DEPTH_STATE_EXPLAINS_REJECTION
  - funnel: `{"v52_extremes": 1487, "v52_flow_depth_pass": 0, "v52_inflections": 21, "v52_oi_contraction_pass": 9, "v52_peer_context_ready": 40236, "v52_same_timestamp_peer_uses": 0, "v52_setups": 0, "v53_catchup_context": 0, "v53_catchup_flow_pass": 0, "v53_catchup_oi_pass": 0, "v53_catchup_setups": 0}`
  - mechanisms: `{}`
- **v53** — IMPLEMENTATION_VALID; trades=0, return=0.0, PF=0.0, reachability=FLOW_DEPTH_STATE_EXPLAINS_REJECTION
  - funnel: `{"v52_extremes": 1487, "v52_flow_depth_pass": 0, "v52_inflections": 21, "v52_oi_contraction_pass": 9, "v52_peer_context_ready": 40236, "v52_same_timestamp_peer_uses": 0, "v52_setups": 0, "v53_catchup_context": 8, "v53_catchup_flow_pass": 0, "v53_catchup_oi_pass": 5, "v53_catchup_setups": 0}`
  - mechanisms: `{}`

Paired comparison: `{"causal_comparison": "INTERPRETABLE", "hypothesis_attribution": "CATCHUP_STATE_NOT_EXECUTED", "net_pnl_delta_v53_minus_v52": 0.0, "residual_episode_preservation": {"matched_residual_episodes": 0, "matches": [], "v52_episode_preservation_fraction": null, "v52_residual_trades": 0, "v53_residual_trades": 0}, "total_return_delta_v53_minus_v52": 0.0, "trade_delta_v53_minus_v52": 0, "v52_residual_mechanism": {}, "v53_catchup_mechanism": {}, "v53_residual_mechanism": {}}`

## week-2024-01

- **v52** — IMPLEMENTATION_VALID; trades=1, return=0.008294967589199898, PF=None, reachability=ECONOMIC_EPISODES_OBSERVED
  - funnel: `{"v52_extremes": 1110, "v52_flow_depth_pass": 1, "v52_inflections": 17, "v52_oi_contraction_pass": 4, "v52_peer_context_ready": 40296, "v52_same_timestamp_peer_uses": 0, "v52_setups": 1, "v53_catchup_context": 0, "v53_catchup_flow_pass": 0, "v53_catchup_oi_pass": 0, "v53_catchup_setups": 0}`
  - mechanisms: `{"POSITION_BUILDING_BALANCE_ACCEPTANCE": {"gross_loss": 0, "gross_profit": 829.49675892, "largest_winner_share": 1.0, "losses": 0, "net_pnl": 829.49675892, "profit_factor": null, "trades": 1, "wins": 1}}`
- **v53** — IMPLEMENTATION_VALID; trades=1, return=0.008294967589199898, PF=None, reachability=ECONOMIC_EPISODES_OBSERVED
  - funnel: `{"v52_extremes": 1110, "v52_flow_depth_pass": 1, "v52_inflections": 17, "v52_oi_contraction_pass": 4, "v52_peer_context_ready": 40296, "v52_same_timestamp_peer_uses": 0, "v52_setups": 1, "v53_catchup_context": 10, "v53_catchup_flow_pass": 0, "v53_catchup_oi_pass": 6, "v53_catchup_setups": 0}`
  - mechanisms: `{"POSITION_BUILDING_BALANCE_ACCEPTANCE": {"gross_loss": 0, "gross_profit": 829.49675892, "largest_winner_share": 1.0, "losses": 0, "net_pnl": 829.49675892, "profit_factor": null, "trades": 1, "wins": 1}}`

Paired comparison: `{"causal_comparison": "INTERPRETABLE", "hypothesis_attribution": "CATCHUP_STATE_NOT_EXECUTED", "net_pnl_delta_v53_minus_v52": 0.0, "residual_episode_preservation": {"matched_residual_episodes": 0, "matches": [], "v52_episode_preservation_fraction": null, "v52_residual_trades": 0, "v53_residual_trades": 0}, "total_return_delta_v53_minus_v52": 0.0, "trade_delta_v53_minus_v52": 0, "v52_residual_mechanism": {}, "v53_catchup_mechanism": {}, "v53_residual_mechanism": {}}`

## week-2024-03

- **v52** — IMPLEMENTATION_VALID; trades=0, return=0.0, PF=0.0, reachability=SETUPS_EXIST_BUT_CONFIRMATION_GEOMETRY_EXECUTION_OR_SLOT_REJECTS
  - funnel: `{"v52_extremes": 1051, "v52_flow_depth_pass": 1, "v52_inflections": 16, "v52_oi_contraction_pass": 9, "v52_peer_context_ready": 40313, "v52_same_timestamp_peer_uses": 0, "v52_setups": 1, "v53_catchup_context": 0, "v53_catchup_flow_pass": 0, "v53_catchup_oi_pass": 0, "v53_catchup_setups": 0}`
  - mechanisms: `{}`
- **v53** — IMPLEMENTATION_VALID; trades=0, return=0.0, PF=0.0, reachability=SETUPS_EXIST_BUT_CONFIRMATION_GEOMETRY_EXECUTION_OR_SLOT_REJECTS
  - funnel: `{"v52_extremes": 1051, "v52_flow_depth_pass": 1, "v52_inflections": 16, "v52_oi_contraction_pass": 9, "v52_peer_context_ready": 40313, "v52_same_timestamp_peer_uses": 0, "v52_setups": 1, "v53_catchup_context": 11, "v53_catchup_flow_pass": 0, "v53_catchup_oi_pass": 4, "v53_catchup_setups": 0}`
  - mechanisms: `{}`

Paired comparison: `{"causal_comparison": "INTERPRETABLE", "hypothesis_attribution": "CATCHUP_STATE_NOT_EXECUTED", "net_pnl_delta_v53_minus_v52": 0.0, "residual_episode_preservation": {"matched_residual_episodes": 0, "matches": [], "v52_episode_preservation_fraction": null, "v52_residual_trades": 0, "v53_residual_trades": 0}, "total_return_delta_v53_minus_v52": 0.0, "trade_delta_v53_minus_v52": 0, "v52_residual_mechanism": {}, "v53_catchup_mechanism": {}, "v53_residual_mechanism": {}}`

## Interpretation discipline

- An implementation-blocked run carries no economic conclusion.
- A higher v53 return does not support the catch-up hypothesis unless catch-up episodes themselves explain the paired improvement.
- Genuine v52 residual episodes should remain preserved unless an explicit mutually exclusive state competes for the same causal event.
- Zero trades are decomposed through the reachability funnel rather than called a strategy failure.
- These inspected diagnostic windows are development data; any supported structure must move to new data without threshold tuning.
