# Task 4 Brief — R87 + role-level R60 126-day positive trend gate

## Objective

Implement ai_rotation_r88_r81_role_r60_gate as the next single-hypothesis Challenger against current research Champion R87. Preserve repaired R81 dynamic representatives, R86 50% transition cap, and R87 role Top3-entry/Top4-exit buffer; add only the R60-style causal 126-trading-day adjusted-return-positive gate at role level.

## Hypothesis

Rejecting a currently selected R87 role when its current R81 representative has a non-positive 126-trading-day adjusted return will reduce prolonged adverse exposure and drawdown without lowering validation annual return or Sharpe.

## Scope and invariants

- Do not modify R60, R81, R86, R87, public runner, PIT/data contracts, or execution semantics.
- Use only current R81 representative codes; do not reselect representatives, re-cluster, or import R60/R59 member/cluster state.
- The 126-day window must be causal through signal close and require exactly 127 positive adjusted-close observations; missing/invalid/non-positive return is fail-closed and role-ineligible.
- Preserve R87 hysteresis, including role-only state and epoch reset, and preserve R86 post-decision transition cap.
- Add an independent strategy package, focused tests, registry/catalog entry, and implementation hash.
- Fixed interval, snapshot, folds, execution contract and research-only caveat remain unchanged.

## Tests and batch

- TDD RED then GREEN for causal 126-day return, positive/negative/missing gate, role-only application, and preservation of R87 cap/buffer.
- Run focused and relevant regressions plus fresh independent review.
- Run exactly one paired batch: R87 Champion vs R88 Challenger over 20130329..20220729, then fold analysis and gate decision.
