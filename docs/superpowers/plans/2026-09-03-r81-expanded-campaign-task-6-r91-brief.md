# Task 6 Brief — R88 + role-level R73 multi-horizon rank

## Objective

Implement ai_rotation_r91_r81_role_r73_multi_horizon as a single-hypothesis Challenger against current Champion R88. Preserve R81 representatives, R86 50% cap, R87 Top3/Top4 role hysteresis, and R88 126-day positive gate; replace only role ranking with equal-weight rank aggregation of causal 60/120/240-trading-day adjusted returns.

## Hypothesis and boundaries

The multi-horizon rank should improve robustness and drawdown relative to R88. It may use only current R81 representative codes, exact causal windows requiring horizon+1 valid adjusted closes, deterministic role-ID ties, and fail-closed incomplete roles. No re-clustering, representative changes, or member/cluster state.

Add independent package/tests/registry/catalog and role-universe prefixes in both routing layers. Run RED/GREEN and fresh review, then one paired R88 vs R91 batch over the frozen interval. No deployment.
