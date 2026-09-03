# Task 5 Brief — R88 + role-level R61 dual-horizon score

## Objective

Implement ai_rotation_r90_r81_role_r61_dual_horizon as the next single-hypothesis Challenger against current Champion R88. Preserve repaired R81 representatives, R86 transition cap, R87 role hysteresis, and R88's causal 126-day positive gate; replace only the role ranking score with a 50/50 standardized short/medium horizon score.

## Hypothesis

Combining the current R81 short-horizon role score with the current representative's causal 126-day adjusted return at equal standardized weights will improve validation risk-adjusted return and reduce drawdown relative to the current Champion.

## Scope and invariants

- Do not modify R61, R81, R86, R87, R88, public runner, PIT/data contracts, or execution semantics.
- Current representative mapping and lifecycle remain unchanged; no cluster/member state.
- Short component is the existing R81 role score; medium component is causal 126-day adjusted return requiring 127 valid positive adjusted closes. Standardize only complete valid current-role candidates with deterministic role-ID ties; fail closed for missing data.
- Preserve R88 positive gate and R87 hysteresis ordering, and keep diagnostics/artifacts consistent.
- Add independent package, focused tests, registry/catalog entry, and role-universe routing prefixes in both routing layers.
- Fixed interval/snapshot/folds/execution contract remain unchanged.

## Tests and batch

- TDD RED then GREEN for z-score fusion, missing-data exclusion, deterministic ties, role-only scope, and preservation of upstream cap/gate/buffer.
- Fresh independent review before one paired batch: R88 Champion vs R90 Challenger, followed by fold analysis and gate decision.
