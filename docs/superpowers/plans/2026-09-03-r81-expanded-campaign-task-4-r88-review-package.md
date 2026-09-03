# Task 4 R88 Implementation Review

## Verdict

**CHANGES_REQUIRED**

R88 的策略实现和聚焦回归总体符合 brief，但发现 1 个 P1，不能在修复前进行正式 paired backtest。

## Scope reviewed

- `docs/superpowers/plans/2026-09-03-r81-expanded-campaign-task-4-r88-brief.md`
- R88 package: `agent/backtest/fund_rotation/strategies/ai_rotation_r86_r81_transition_cap_50/r88_r81_role_r60_gate.py`
- R88 tests: `agent/tests/fund_rotation/test_ai_rotation_r88_r81_role_r60_gate.py`
- R87/R86/R81 inheritance and artifact paths
- registry/catalog registration
- batch-service and runner role-universe routing

No long backtest was run.

## Findings

### P1 — R88 未加入经济角色池路由前缀

- **Evidence:** `agent/src/stockpred/fund_rotation/batch_service.py:372-386` and `agent/scripts/run_r81_combination_batch.py:29-44` include R79–R87 role prefixes, but not `ai_rotation_r88_r81_role_r60_gate`.
- **Impact:** A batch containing R88 without another earlier role-prefixed variant selects `snapshot.universe_codes` instead of `snapshot.role_universe_codes`. This violates the role-only execution scope and can make current R81 representatives unavailable to the execution universe. The required R87-vs-R88 pair happens to contain R87 and therefore masks the defect, but R88 is not independently correctly routable.
- **Minimal fix:** Append the exact R88 prefix to both existing role-prefix tuples, preserving all existing entries; add a focused R88-only routing regression. Do not alter PIT or execution semantics.

## Verified implementation properties

- R88 is a new strategy ID and is appended to the explicit registry/catalog; the catalog test preserves prior IDs and checks a non-empty implementation hash.
- R88 inherits `AiRotationR87R81RoleRankBufferStrategy` and its session inherits `EconomicRoleR81RoleRankBufferSession`; the R87 session in turn inherits the R86 transition-cap session. No existing R60/R81/R86/R87 implementation was changed by the R88 package.
- The gate is applied to `self._representatives`, i.e. current R81 dynamic representative codes. It does not reselect representatives, recluster, or import R60/R59 cluster/member state.
- `compute_adjusted_return_126d` applies the causal cutoff through `signal_date`, uses adjusted OHLC via the R60 helper, takes exactly the last 127 observations, and requires valid positive adjusted closes. Non-positive, missing, insufficient, invalid, or non-finite data is not qualified; the role gate is therefore fail-closed.
- The gate runs before R87 Top3-entry/Top4-exit selection, while R86's post-decision 50% positive-exposure transition cap remains in the downstream `evaluate` chain.
- R88 adds medium-trend diagnostics to the decision and synchronizes them into the `decisions` artifact entry after the upstream decision is created.

## Tests run

Command:

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_ai_rotation_r88_r81_role_r60_gate.py agent/tests/fund_rotation/test_ai_rotation_r87_r81_role_rank_buffer.py agent/tests/fund_rotation/test_ai_rotation_r86_r81_transition_cap_50.py agent/tests/fund_rotation/test_economic_role_rotation.py agent/tests/fund_rotation/test_strategy_catalog.py -q
```

Result: **55 passed, 1 warning**. The warning is the pre-existing pytest cache write ACL warning.

The tests cover positive/negative/missing representative behavior, causal cutoff with a 127-observation result, pipeline inheritance, R87 buffer metadata, R86 cap metadata, economic-role regression, and catalog registration. They do not yet independently prove R88-only role routing or artifact equality with a full session fixture.

## Deferred P2 risks

- Add a future-row perturbation test proving the 126-day result is invariant to rows after the signal close.
- Add an explicit exactly-126-observation/invalid-adjustment test and a session-level artifact-vs-returned-diagnostics equality test.
- Existing Windows temp/cache ACL warnings remain environmental and are not attributed to R88.

## Backtest decision

Do not run the authorized R87-vs-R88 paired batch until the P1 routing fix is independently reviewed and focused tests pass. No Champion decision is made.
