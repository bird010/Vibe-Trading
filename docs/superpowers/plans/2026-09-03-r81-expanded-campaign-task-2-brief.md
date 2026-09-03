# Task 2 Brief — R81 + R69 transition cap 50%

## Objective

Implement and validate a new independent strategy ID `ai_rotation_r86_r81_transition_cap_50`.
The only changed behavior is the R69-style weekly transition cap applied after the repaired R81 decision: positive target exposure increases are scaled so their total is at most `0.50`; reductions, unchanged holdings, representative selection, role scoring, lifecycle, fixed-defense eligibility, and cash fallback remain R81 semantics.

## Hypothesis

On the repaired R81 anchor, limiting one-week positive target exposure to 50% will reduce turnover/slippage and drawdown without reducing validation annual return or Sharpe under the frozen comparison contract.

## Scope and invariants

- Do not modify R81, R69, R70, public runner behavior, PIT/data contracts, or execution semantics.
- Create only the R86 package, focused tests, and append-only registry/catalog entries.
- Reuse the existing pure `apply_transition_cap` behavior only if its semantics remain exact; do not import R69/R39 session state or representative-selection state.
- Keep the evaluation interval `20130329..20220729`, frozen snapshot, weekly calendar, execution contract, and `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` quality caveat identical to the repaired R81 anchor.
- The decision must remain legal under the target-weight contract: no negative weights, no eligible-pool violations, and weights plus cash must sum to one within tolerance.

## Required tests

1. RED test first: positive additions are capped at 0.50 and excess becomes cash.
2. RED test first: reductions and unchanged positions are not scaled.
3. Verify the new session preserves the upstream R81 decision except for target weights/cash, decision ID, and transition-cap diagnostics.
4. Verify the new descriptor/registry identity is unique and the pipeline advertises the cap.
5. Run focused tests and relevant fund-rotation regression tests before the paired batch.

## Batch requirement

Run exactly one paired Champion-Challenger batch: repaired R81 anchor vs R86, with a fresh idempotency key and the same external writable output root. Persist batch ID, child run IDs, resolved snapshot/fold evidence, raw metrics, gate result, and implementation hash in the campaign ledger. R86 replaces the Champion only if every optimizing-fund-rotation gate passes.

## Review requirement

Use a fresh Luna implementer and a separate fresh Luna reviewer. If review finds P0/P1 issues, resume the same implementer for the fix loop; do not patch the implementation directly in the controller.
