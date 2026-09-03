# Task 6 R91 Fresh Review

## Verdict

**PASS** — no P0/P1 findings. R91 is eligible for the single planned paired backtest against R88. This review did not run a long backtest and did not modify implementation code.

## Scope and evidence

- Reviewed the Task 6 brief and the `optimizing-fund-rotation` campaign constraints.
- Reviewed R91 implementation, tests, registry/catalog registration, both role-universe routing layers, and the R88/R87/R86/R81 inheritance chain.
- Focused tests:
  - `test_ai_rotation_r91_r81_role_r73_multi_horizon.py`
  - `test_strategy_catalog.py`
  - `test_ai_rotation_r87_r81_role_rank_buffer.py`
  - `test_ai_rotation_r88_r81_role_r60_gate.py`
  - `test_r81_runner_output_root.py`
  - Result: **16 passed**, one pre-existing pytest cache ACL warning.

## Review checklist

### Data lookback and causality

- R91 requests `adjusted_closes(lookback=241)`, exactly `240 + 1` observations for the longest horizon. The causal data view itself applies the signal-date cutoff before returning data; the helper applies a second explicit `<= signal_date` cutoff and sorts the retained dates.
- Each horizon uses the last `horizon + 1` observations and computes `end / start - 1`. Non-positive, non-finite, or missing values invalidate that horizon. A 240-row fixture correctly yields no 240-day result, while shorter horizons remain valid.
- The inherited R88 127-observation medium-trend gate remains causal and unchanged in substance.

### Fail-closed behavior and ranking

- A role is eligible for the multi-horizon ranking only when it has a valid result for all 60/120/240 horizons. Incomplete roles are marked `INCOMPLETE_HORIZON` and excluded from the ranked eligible set.
- Ranking direction is correct: larger return receives the smaller rank; aggregate rank is the equal-weight mean of the three horizon ranks.
- Ties are deterministic through the role ID as the secondary key. Complete roles are also returned in deterministic aggregate-rank/role-ID order.
- Representative selection is not recomputed: R91 consumes the current R81 representatives only. No re-clustering, member/cluster state, or future-date data access was introduced.

### Preservation of upstream semantics

- R91 inherits R88 and replaces only role ranking in `_rank_roles`.
- R88's 126-day positive representative gate is reapplied and required for `valid` roles.
- R87's Top3-entry/Top4-exit hysteresis and R86's 50% positive transition cap remain on the execution path.
- R81 lifecycle, dynamic representative, defense fallback, and decision transaction semantics remain inherited rather than copied or altered.

### Diagnostics and artifacts

- R91 adds `role_multi_horizon_rank` diagnostics with rule, horizons, required observations, representative, gate status, per-horizon ranks, aggregate rank, and incomplete-horizon status.
- The session updates the final decision diagnostics and the corresponding `_decision_log` entry, including the independent R91 decision ID. This keeps the returned decision and persisted decisions artifact aligned.

### Registration and routing

- Registry/catalog contains one independent ID: `ai_rotation_r91_r81_role_r73_multi_horizon`.
- `batch_service.py` and `run_r81_combination_batch.py` both explicitly route the R91 ID prefix to `role_universe_codes`; the R91-only routing test confirms that `513100.SH` is retained.
- The module is physically located in the existing R86 package directory due the workspace directory constraint, but its descriptor ID, registry entry, and routing prefix are independent and do not alias R86.

## Findings

### P0

None.

### P1

None.

### P2 (deferred; does not block the planned paired backtest)

1. R91 is a separate module but not a separate filesystem package directory. The independent ID and routing behavior are verified; moving it to a dedicated package would improve maintainability but is not required for this research-only run.
2. The R91-specific suite does not independently perturb future dates or exercise multi-week state transfer. The inherited causal implementation and existing regression coverage are sufficient for this review gate, but these tests should be added before any deployment consideration.

### P3

- Pytest emitted the existing cache-write ACL warning for `.pytest_cache`; it did not affect test execution.

## Final review decision

**PASS.** Proceed with exactly one frozen paired backtest, R88 Champion versus R91 Challenger, over the campaign interval `20130329–20220729`. Do not promote R91 based on code review; apply the campaign Champion gate only to the paired backtest evidence.
