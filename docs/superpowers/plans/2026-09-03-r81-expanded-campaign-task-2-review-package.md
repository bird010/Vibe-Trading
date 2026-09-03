# Task 2 R86 Implementation Review

## Verdict

**PASS** for the implementation review. No P0 or P1 finding was identified. The R86 implementation is an independent registered strategy and confines its behavioral change to a post-R81-decision 50% positive target-exposure cap.

This is an implementation verdict only. The optimizing-fund-rotation campaign is not qualified until the required single paired repaired-R81-vs-R86 batch has terminal artifacts, frozen-contract evidence, and a gate decision recorded in the campaign ledger.

## Scope and evidence

- Reviewed commit `072b3c874e6740bf241b8ae86734fd2971a0a5ac`, parent `3dc9e8b0d9a0f18199f1174a24152fd04aa5e968`.
- Current tracked worktree has no implementation diff relative to `HEAD`; only pre-existing/untracked plan documents are present.
- Reviewed against `docs/superpowers/plans/2026-09-03-r81-expanded-campaign-task-2-brief.md` and the `optimizing-fund-rotation` rules.
- `git diff --check 072b3c87^ 072b3c87` passed.

## Checks

### Independent identity and scope — pass

`agent/backtest/fund_rotation/strategies/ai_rotation_r86_r81_transition_cap_50/` is a separate package. Its descriptor is uniquely named `ai_rotation_r86_r81_transition_cap_50`, deterministic, ETF-scoped, and registered exactly once in `default_fund_rotation_strategies()` (`strategy.py:24-34`, `registry.py:243-245,331`). R81, R69, and the public runner are not modified by this commit.

R86 subclasses the shared economic-role session but constructs it with the repaired R81 descriptor ID and explicitly sets `REPRESENTATIVE`/`DYNAMIC` semantics (`strategy.py:37-47`). This preserves the repaired R81 fixed-defense eligibility branch and upstream role/lifecycle behavior.

### Post-decision-only cap — pass

`evaluate()` snapshots the prior state, calls the upstream economic-role evaluation, and only then applies `apply_transition_cap` to target weights (`strategy.py:49-54`). The imported helper is the existing pure R69 helper; it does not import R69 session state or representative-selection state. Its behavior scales only positive increases, leaves reductions and unchanged holdings unscaled, and computes residual cash from the adjusted total.

The replacement changes only target weights, cash, decision ID, and diagnostics (`strategy.py:55-68`). The cap is fixed at `0.50` (`strategy.py:40`) and is advertised in the decision pipeline (`strategy.py:98-103`).

### State and artifacts — pass, with a non-blocking coverage gap

`_patch_artifacts()` updates `_previous_weights` to the capped target state and synchronizes the last decision-log row's target weights, cash, and diagnostics (`strategy.py:71-90`). It also updates candidate-row target weights in the decision trace. This prevents the next week's cap from using the uncapped upstream decision and keeps emitted decision artifacts aligned with the returned decision.

The focused session test verifies upstream action/reason/diagnostics preservation, capped weights/cash, and R86 decision identity (`test_ai_rotation_r86_r81_transition_cap_50.py:56-90`). A P2 coverage gap remains: there is no test for the uncapped path or a second sequential evaluation proving the patched state is used as the next week's `previous_weights`. Static inspection finds the state update correct; this does not block implementation approval.

### PIT, eligibility, and contract legality — pass by composition

R86 performs no new data lookup, date shift, universe construction, or representative selection. All PIT and signal-date eligibility work remains in the repaired R81 upstream session. The cap only interpolates between the already-legal prior and upstream target weights, never creates a new positive code, clamps negative inputs defensively, and returns residual cash. Given the upstream target contract, the resulting weights remain non-negative and sum with cash to one within the existing tolerance. The tests cover the principal legal cap cases.

### Determinism and registry/catalog — pass

The cap uses sorted code iteration and `math.fsum` through the reused pure helper; diagnostics contain only deterministic scalar fields. Focused identity/pipeline tests pass. Both strategy registry/catalog expected-ID lists include the new ID append-only, and the strategy catalog regression passes.

## Test evidence

Passed:

- `pytest agent/tests/fund_rotation/test_ai_rotation_r86_r81_transition_cap_50.py agent/tests/fund_rotation/test_economic_role_rotation.py -q` → **22 passed**, 1 pre-existing pytest cache permission warning.
- `pytest agent/tests/fund_rotation/test_strategy_catalog.py -q` → **23 passed**, 1 pre-existing pytest cache permission warning.

The combined catalog API command reached 23 passed but ended with 16 setup errors after `-p no:tmpdir` removed the required `tmp_path` fixture; the first run using `--basetemp` instead hit a Windows ACL denial while pytest cleaned that directory. This is test-environment failure, not a reported R86 assertion failure. The catalog API suite should be rerun in a writable pytest temp root before campaign publication.

## P0–P3 findings

- P0: none.
- P1: none.
- P2: add a focused sequential-state test and an uncapped/no-op cap test; rerun the catalog API suite with a genuinely writable pytest temp directory.
- P3: pytest cache warnings are caused by the existing workspace ACL and are unrelated to R86.

## Campaign disposition

Implementation may proceed to the one required paired repaired-R81-vs-R86 batch under the frozen interval, snapshot, execution contract, and research-only universe caveat. Do not promote R86 or claim Champion replacement from this review alone.
