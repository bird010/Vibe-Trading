# Task 3 R87 Implementation Review

## Verdict

**CHANGES_REQUIRED**. Two P1 findings block the R87 implementation review from passing. No P0 finding was identified. The required R86-vs-R87 paired backtest must not run until these implementation issues are resolved and re-reviewed.

## Scope and evidence

- Reviewed against `docs/superpowers/plans/2026-09-03-r81-expanded-campaign-task-3-brief.md` and the `optimizing-fund-rotation` rules.
- Reviewed the current uncommitted R87 implementation, registry/catalog changes, and focused tests.
- No implementation files were modified by this review.
- `git diff --check` completed without whitespace errors.

## Findings

### P1 — R87 mutates a process-global scoring function

`r87_role_rank_buffer.py:71-92` replaces `_role_module.rank_scores` for the duration of evaluation. This is process-global module state, not session-local state. Concurrent evaluations, nested sessions, or an exception/interleaving in another strategy can observe the R87 wrapper and have their cluster/member tie-break semantics silently replaced by role-only behavior. That violates the auditability/determinism requirement for a Champion-Challenger backtest and makes the implementation unsafe when more than one session exists in a process.

The wrapper also discards the caller's `cluster_members` argument (`:75-77`) and forces the canonical scorer to use `{}` (`:77`). The intended R87 transformation should be applied at the role-selection boundary without monkeypatching a shared module symbol. Refactor the integration to use an explicit session-local ranking/selection path, or an equivalent dependency-injection seam, and add a test proving another session/call retains the original scorer during R87 evaluation.

### P1 — Returned diagnostics and persisted decision artifact diverge

R87 calls `super().evaluate()` inside the context (`:99-100`). R86's `evaluate()` then calls `_patch_artifacts()` before returning. R87 subsequently adds `diagnostics["role_rank_buffer"]` and returns a replaced decision (`:101-103`), but does not update the already-written `_decision_log` row. Therefore the returned `TargetWeightDecision` contains `role_rank_buffer`, while the `decisions` artifact written by `finalize()` lacks that diagnostic for the same signal date.

This breaks the required diagnostics/artifacts audit trail and can make a replay or report unable to explain the selected role ordering. Ensure the final R87 diagnostics are written into the same decision log/trace artifact that is emitted, and add an integration test comparing the returned decision diagnostics with the last persisted decision row.

## Passed checks

- Focused R87, strategy catalog, and economic-role regression tests: **46 passed**, one pre-existing `PytestCacheWarning` caused by workspace ACLs.
- R87 is registered once and has a unique catalog identity.
- The pure selector is role-only: it consumes role IDs and valid role scores, applies Top3 entry/Top4 exit behavior, excludes invalid roles, and uses deterministic role-ID ordering for retained roles.
- R87 inherits `EconomicRoleR81TransitionCap50Session`; the public pipeline advertises the R86 50% cap, and the cap layer remains downstream of the R81 decision.
- No R81, R86, R63, public runner, PIT contract, or execution implementation change was made by the reviewed R87 files.

## P2/P3 observations

- P2: tests do not cover two consecutive evaluations with state continuity, a refresh-epoch reset through the session's real `evaluate()` path, or the uncapped/no-op transition-cap path.
- P2: diagnostics do not explicitly record the final selected role list or the role-buffer artifact separately; this becomes especially important after the P1 artifact mismatch is fixed.
- P3: the existing pytest cache permission warning is environmental and non-blocking.

## Disposition

R87 is **not approved** for the required paired backtest and cannot replace R86. Send the two P1 findings through the designated implementer fix loop, then run a fresh review before any long backtest.
