# Task 3 Brief — R86 + role-level R63 rank buffer

## Objective

Implement and validate `ai_rotation_r87_r81_role_rank_buffer` as the next Champion-Challenger round. The current research Champion is R86, so R87 must retain R86's repaired R81 upstream, 50% one-week positive-exposure cap, lifecycle, defense eligibility, and execution behavior, while adding exactly one role-level R63-style hysteresis rule.

## Hypothesis

Selecting roles with Top3 entry and Top4 exit hysteresis will reduce role-boundary churn and drawdown without lowering validation annual return or Sharpe. The rank buffer is applied to R81's existing `ROLE_IDS` and current role scores only; it must not import cluster IDs or R63's R59/R39 state.

## Scope and invariants

- Do not modify R63, R81, R86, public runner, PIT/data contracts, or execution semantics.
- Preserve R81 dynamic representative mapping and role-internal lifecycle; only the role-selection boundary changes.
- Reset the role hysteresis state on a fresh session/role refresh epoch. Invalid/missing role scores are ineligible and cannot be retained.
- Use deterministic role-ID tie breaking, explicit diagnostics, and legal target weights/cash.
- Create an independent package, focused tests, registry/catalog append-only entries, and implementation hash.
- Fixed interval/snapshot/folds/execution remain those in the campaign spec; confirmation data remains excluded.

## Required tests

1. RED test first: a previously selected role at rank 4 is retained, while rank 5 is forced out and the remaining slots are filled by current ranking.
2. RED test first: epoch reset clears stale role selections; invalid roles are not retained.
3. RED test first: deterministic tie behavior and role-level-only state (no cluster state).
4. Verify R87 preserves the R86 transition-cap layer and R81 representative/lifecycle artifacts.
5. Run focused, registry/catalog, and relevant economic-role regressions before the batch.

## Batch requirement

Run exactly one paired batch: current R86 Champion vs R87 Challenger over `20130329..20220729`. Record terminal state, raw metrics, five-fold evidence, gate decision, and implementation hash in the expanded append-only ledger. R87 replaces R86 only if every gate passes.

## Review requirement

Use a fresh Luna implementer and a separate fresh Luna reviewer. Review P0/P1 findings through the same implementer fix loop; do not patch implementation findings directly in the controller.
