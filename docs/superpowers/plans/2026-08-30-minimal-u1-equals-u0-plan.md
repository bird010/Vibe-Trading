# Minimal U1 Equals U0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **历史版本说明：** 本计划已由 `docs/superpowers/plans/2026-08-30-research-permissive-u1-plan.md` 增补并取代。当前执行以增补中的 research-only、可选 identity/PIT 证据契约为准；本文件中的旧版严格 fail-closed 表述仅保留作历史记录。

**Goal:** Keep U1 as an auditable identity layer while deriving it directly from the frozen U0 snapshot with the same eligible-code set.

**Architecture:** Reuse the existing `UniverseResolver.resolve()` and `_identity_key()` logic. Change only the U1 projection so it copies U0 membership, retains identity diagnostics, and records whether the identity evidence is complete and conflict-free; update the existing Shadow manifest validator to verify this contract. Clustering, strategy logic, and the shared Runner remain unchanged.

**Tech Stack:** Python 3, pandas, pytest, immutable JSON/Markdown experiment artifacts, existing `PITFundMaster`/`UniverseResolver` implementation.

## Global Constraints

- U1 must remain a separate semantic layer and must be derived only from the frozen U0 snapshot.
- For every snapshot, `u1.eligible_codes` must equal `u0.eligible_codes`; missing or conflicting optional identity evidence permits research-only execution and must block promotion/deployment.
- `u1_equals_u0` records result-set equality only; it is independent of identity/PIT verification and must not be used as a substitute for those diagnostics.
- Clustering continues to consume U1; do not change clustering algorithms, R39, strategy behavior, or the shared Runner.
- Reuse existing identity keys, hashes, membership records, snapshot fingerprints, and immutable artifact rules.
- Every task ends with focused tests, `git diff --check`, a Luna 5.6 high-reasoning review, and a separate commit; P0/P1 findings block the next task.

---

## File Map

- Modify `agent/backtest/fund_rotation/pit_universe.py`: make `_u1_snapshot()` an explicit U0-to-U1 same-set projection while preserving identity diagnostics and research-only quality status for optional-evidence gaps.
- Modify `experiments/fund_rotation_research_validity/pit_identity.py`: describe and serialize the same-set derivation evidence through the existing snapshot payload.
- Modify `experiments/fund_rotation_research_validity/start_r40_shadow.py`: validate equal U0/U1 eligible sets and the new derivation evidence instead of requiring representative-only membership.
- Modify `agent/tests/fund_rotation/test_pit_identity_layers.py`: replace deduplication expectations with same-set derivation, and cover valid, duplicate, and missing-identity cases.
- Modify `agent/tests/fund_rotation/test_runner_contract_integration.py`: verify the production U1 adapter exposes the complete U0 eligible set.
- Modify `agent/tests/fund_rotation/test_r40_shadow_start.py`: provide and validate a canonical same-set U0/U1 manifest.
- Modify `experiments/fund_rotation_research_validity/acceptance_matrix.md`: align the current acceptance wording with the approved U1 contract.

### Task 1: Add failing tests for the same-set contract

**Files:**
- Modify: `agent/tests/fund_rotation/test_pit_identity_layers.py`
- Modify: `agent/tests/fund_rotation/test_runner_contract_integration.py`
- Modify: `agent/tests/fund_rotation/test_r40_shadow_start.py`

**Interfaces:**
- Consumes: existing `UniverseResolver.resolve_identity_layers()`, `FundRotationPITUniverseAdapter`, and `_valid_u1_envelope()` behavior.
- Produces: executable expectations for `u1.eligible_codes == u0.eligible_codes`, `coverage_diagnostics["u1_equals_u0"]`, and `U1_DERIVED_FROM_U0` membership.

- [ ] **Step 1: Replace resolver expectations that currently require identity representatives.**

Use the existing `_identity_row()` fixtures and change the valid case to assert:

```python
assert layers.u1.eligible_codes == layers.u0.eligible_codes
assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
assert all(
    item.included and item.reason_code == "U1_DERIVED_FROM_U0"
    for item in layers.u1.membership
    if item.ts_code in layers.u0.eligible_codes
)
```

For duplicate identity and missing identity fixtures, assert the same eligible-code set, `u1_equals_u0 is True`, the explicit `CONFLICT`/`UNAVAILABLE` identity status, and research-only quality. Keep the existing duplicate and missing counts as diagnostics; core data errors remain `PIT_INVALID`.

- [ ] **Step 2: Run the focused tests and verify the old implementation fails.**

Run from `E:\code\stock\Vibe-Trading`:

```powershell
python -m pytest agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_r40_shadow_start.py -q
```

Expected: FAIL because the current U1 projection removes duplicate identities and missing-identity members instead of preserving U0's eligible-code set.

- [ ] **Step 3: Commit the red tests.**

Run from `E:\code\stock\Vibe-Trading`:

```powershell
git add -- agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_r40_shadow_start.py
git commit -m "test: define same-set U1 derivation contract"
```

After the commit, dispatch one `gpt-5.6-luna` reviewer with `reasoning_effort=high` and require an explicit P0/P1 result before Task 2.

### Task 2: Implement the minimal U0-to-U1 projection

**Files:**
- Modify: `agent/backtest/fund_rotation/pit_universe.py:692-809`

**Interfaces:**
- Consumes: `PITUniverseSnapshot` from `_snapshot_from_resolution()`, the existing canonical identity mapping, and existing stable hash helpers.
- Produces: a `PITUniverseSnapshot(layer="U1")` with the same `eligible_codes` as U0, unchanged identity mapping, duplicate/missing diagnostics, `u1_equals_u0`, and explicit research-only quality for optional identity defects.

- [ ] **Step 1: Change `_u1_snapshot()` to retain every U0 eligible code.**

Keep the existing grouping and representative calculation solely as diagnostics/hash evidence. For each U0 membership item, preserve exclusions exactly; for each U0 eligible code, emit:

```python
UniverseMembership(
    ts_code=code,
    included=True,
    reason_code="U1_DERIVED_FROM_U0",
    identity_key=u0.identity_mapping.get(code),
    layer="U1",
)
```

Set `eligible_codes=tuple(u0.eligible_codes)` and add these coverage values:

```python
"u1_equals_u0": True,
"u0_available_count": len(u0.eligible_codes),
"u1_available_count": len(u0.eligible_codes),
```

Set U1 quality to research-only when optional identity evidence is missing or conflicting; retain `PIT_INVALID` for core data errors. Keep existing identity hash inputs and fingerprint inputs deterministic, including the representative mapping as identity evidence.

- [ ] **Step 2: Run the focused tests and verify they pass.**

```powershell
python -m pytest agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py -q
```

Expected: PASS for resolver, adapter, deterministic hash, duplicate, and missing-identity tests.

- [ ] **Step 3: Run the related PIT universe regression tests.**

```powershell
python -m pytest agent/tests/fund_rotation/test_pit_universe.py agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py -q
```

Expected: PASS with no changes outside the U1 projection contract.

- [ ] **Step 4: Commit the minimal implementation.**

Run from `E:\code\stock\Vibe-Trading`:

```powershell
git add -- agent/backtest/fund_rotation/pit_universe.py
git commit -m "feat: derive U1 from U0 with same eligible set"
```

After the commit, dispatch one `gpt-5.6-luna` reviewer with `reasoning_effort=high`; do not proceed while any P0/P1 issue remains.

### Task 3: Update the Shadow manifest validator to enforce the new contract

**Files:**
- Modify: `experiments/fund_rotation_research_validity/start_r40_shadow.py:200-300`
- Modify: `agent/tests/fund_rotation/test_r40_shadow_start.py:220-300`

**Interfaces:**
- Consumes: serialized U0/U1 snapshots produced by `_snapshot_payload()` and the stable hash formula from `pit_universe.py`.
- Produces: fail-closed validation for valid same-set U1 manifests and rejection of representative-only legacy manifests.

- [ ] **Step 1: Change the test fixture to use two distinct complete identities.**

Build U0 and U1 with the same `eligible_codes`, set U1 membership for both codes to `included=True` and `reason_code="U1_DERIVED_FROM_U0"`, add `"u1_equals_u0": True` to U1 coverage diagnostics, and calculate the U1 identity hash/fingerprint using the existing `representatives` evidence for both identities.

- [ ] **Step 2: Run the validator test before changing production code.**

```powershell
python -m pytest agent/tests/fund_rotation/test_r40_shadow_start.py::test_start_shadow_a_accepts_canonical_u0_to_u1_derivation -q
```

Expected: FAIL because the validator currently expects U1 to contain only the minimum code per identity and legacy representative reason codes.

- [ ] **Step 3: Update `_valid_u1_envelope()` with same-set checks.**

Replace representative-only assertions with these checks:

```python
if set(u1["eligible_codes"]) != set(u0["eligible_codes"]):
    return False
if u1["coverage_diagnostics"].get("u1_equals_u0") is not True:
    return False
```

For every U0 eligible membership item, require U1 to contain the same code, `included=True`, the same identity key, and reason `U1_DERIVED_FROM_U0`. Continue using the existing stable identity hash and snapshot fingerprint checks. Do not accept duplicate/missing-identity snapshots as valid Shadow inputs because they are research-only and not verified qualification evidence.

- [ ] **Step 4: Run the Shadow validation tests.**

```powershell
python -m pytest agent/tests/fund_rotation/test_r40_shadow_start.py -q
```

Expected: PASS, including rejection of malformed or representative-only manifests and acceptance of the canonical same-set manifest.

- [ ] **Step 5: Commit the validator change.**

Run from `E:\code\stock\Vibe-Trading`:

```powershell
git add -- experiments/fund_rotation_research_validity/start_r40_shadow.py agent/tests/fund_rotation/test_r40_shadow_start.py
git commit -m "test: validate same-set U1 shadow manifests"
```

After the commit, dispatch one `gpt-5.6-luna` reviewer with `reasoning_effort=high`; require no P0/P1 before Task 4.

### Task 4: Align the experiment serializer, report, and acceptance wording

**Files:**
- Modify: `experiments/fund_rotation_research_validity/pit_identity.py:260-282`
- Modify: `experiments/fund_rotation_research_validity/acceptance_matrix.md:7`
- Modify: `agent/tests/fund_rotation/test_pit_identity_layers.py`

**Interfaces:**
- Consumes: serialized snapshot coverage diagnostics and existing immutable artifact writer.
- Produces: Chinese documentation and regression coverage that state U1 is derived from U0 with equal eligible-code sets, while optional identity defects remain explicit research-only evidence gaps and core defects remain invalid.

- [ ] **Step 1: Add a serializer regression assertion.**

In the existing generated-input test, assert that each valid snapshot's U1 payload contains `coverage_diagnostics["u1_equals_u0"] is True` and that the U0 and U1 `eligible_codes` sets are equal.

- [ ] **Step 2: Update the Chinese report text.**

Change the report wording from “U1 按身份确定性去重” to “U1 从冻结 U0 派生并保持相同 eligible 集合；`u1_equals_u0=true` 仅表示集合相等；身份缺失或冲突时标记 research-only，不能晋级或部署”。 Keep the existing statement that no return, cost, or promotion conclusion is fabricated.

- [ ] **Step 3: Update the acceptance matrix row.**

Describe Batch 1 as “PIT U0/U1 身份证据与同集合派生” and retain the requirement that breadth and later experiments use the frozen U1 identity evidence.

- [ ] **Step 4: Run all focused tests and static checks.**

```powershell
python -m pytest agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_r40_shadow_start.py -q
git diff --check
git diff --stat
```

Expected: all focused tests pass, `git diff --check` emits no errors, and the diff contains only the files listed in this plan.

- [ ] **Step 5: Commit the documentation and serializer changes.**

Run from `E:\code\stock\Vibe-Trading`:

```powershell
git add -- experiments/fund_rotation_research_validity/pit_identity.py experiments/fund_rotation_research_validity/acceptance_matrix.md agent/tests/fund_rotation/test_pit_identity_layers.py
git commit -m "docs: record U1 same-set derivation evidence"
```

After the commit, dispatch the final `gpt-5.6-luna` high-reasoning review, then run the full related fund-rotation test set and report any remaining data-gate limitations separately from code correctness.

## Final Verification

- [ ] Run `python -m pytest agent/tests/fund_rotation -q` from `E:\code\stock\Vibe-Trading`.
- [ ] Confirm no clustering, R39, or shared Runner source files changed.
- [ ] Confirm every valid snapshot has equal U0/U1 eligible sets and `u1_equals_u0=true`.
- [ ] Confirm duplicate or missing optional identity makes the snapshot research-only, retains the same eligible set, and never passes Shadow/promotion qualification.
- [ ] Confirm the final diff is limited to the planned files and contains no P0/P1 review findings.
