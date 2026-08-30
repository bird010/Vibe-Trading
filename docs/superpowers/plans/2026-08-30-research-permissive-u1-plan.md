# Research-Permissive U1 Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Allow the current fund-rotation research flow to run when optional identity/PIT evidence is missing, while preserving explicit research-only status and blocking promotion/deployment.

**Architecture:** Keep `U1` as a separate immutable projection derived from frozen `U0`. Preserve the existing identity mapping, hashes, and membership audit fields, but make the U1 eligible set exactly equal to U0 and move identity/PIT completeness into explicit diagnostics. Downstream research validators accept the research-only envelope; Shadow and promotion validators require verified evidence.

**Tech Stack:** Python 3, pandas, pytest, immutable JSON/Markdown manifests, existing `PITFundMaster`, `UniverseResolver`, fund-rotation Runner, and Shadow validator.

## Global Constraints

- `U1` must remain a separate semantic layer and must be derived only from the frozen `U0` snapshot.
- For every snapshot, `u1.eligible_codes` must equal `u0.eligible_codes`; `u1_equals_u0` records this result-set equality only.
- Bottom index, asset class, region, currency, `known_from`, `valid_from`, `revision_id`, and PIT revision/knowledge fields are optional evidence; missing values must never be fabricated.
- Missing or conflicting optional evidence permits research-only execution but must set `promotion_allowed=false` and `deployment_allowed=false`.
- Core market-data, snapshot-integrity, date-validity, tradability, and accounting failures remain fail-closed.
- Clustering continues to consume U1; do not change clustering algorithms, R39 signal behavior, or the shared Runner execution model.
- Every implementation task ends with focused tests, `git diff --check`, a fresh `gpt-5.6-luna` review using high reasoning, and a separate commit; P0/P1 findings block the next task.
- Every changed line must trace to the optional-evidence contract; do not add an identity fallback based only on `ts_code`.

---

### Task 1: Define the research-permissive U1 behavior with failing tests

**Files:**
- Modify: `agent/tests/fund_rotation/test_pit_identity_layers.py`
- Modify: `agent/tests/fund_rotation/test_runner_contract_integration.py`
- Modify: `agent/tests/fund_rotation/test_r40_shadow_start.py` only where the fixture asserts the new envelope fields

**Interfaces:**
- Consumes: `UniverseResolver.resolve_identity_layers()`, `PITIdentityLayers`, and the existing injected resolver adapter.
- Produces: executable requirements for same-set U1, optional evidence diagnostics, research-only execution, and retained Shadow qualification gates.

- [ ] **Step 1: Add a failing missing-evidence resolver test.**

Add a test using the existing `_identity_row("missing.SH", underlying_index=None, tracking_index=None)` fixture. Assert:

```python
assert layers.u1.eligible_codes == layers.u0.eligible_codes
assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
assert layers.u1.coverage_diagnostics["identity_validation_status"] == "UNAVAILABLE"
assert layers.u1.coverage_diagnostics["pit_evidence_status"] in {"UNAVAILABLE", "PARTIAL"}
assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
assert layers.u1.coverage_diagnostics["deployment_allowed"] is False
assert layers.u1.quality_status is PITQualityStatus.RESEARCH_ONLY_UNVERIFIED_UNIVERSE
assert layers.u1.membership[0].included is True
```

The test must not assert a fabricated identity key; `identity_mapping["missing.SH"]` remains `None`.

- [ ] **Step 2: Add a failing duplicate-evidence resolver test.**

Change the existing duplicate test so both candidates remain included and assert:

```python
assert layers.u1.eligible_codes == layers.u0.eligible_codes
assert layers.u1.coverage_diagnostics["u1_equals_u0"] is True
assert layers.u1.coverage_diagnostics["identity_validation_status"] == "CONFLICT"
assert layers.u1.coverage_diagnostics["research_execution_allowed"] is True
assert layers.u1.coverage_diagnostics["promotion_allowed"] is False
assert all(item.included for item in layers.u1.membership if item.ts_code in layers.u0.eligible_codes)
```

Keep `duplicate_identity_count == 1` and the existing identity hash/fingerprint assertions. The test must prove that conflict is diagnosed rather than silently deduplicated.

- [ ] **Step 3: Add a failing core-error regression.**

Keep one existing invalid-query or unavailable-tradability test and assert that the resulting U1 still has `quality_status is PITQualityStatus.PIT_INVALID` and `research_execution_allowed is False`. This prevents optional-evidence relaxation from weakening core fail-closed behavior.

- [ ] **Step 4: Run the focused red tests.**

Run:

```powershell
python -m pytest agent/tests/fund_rotation/test_pit_identity_layers.py -k "missing_identity or duplicate_identity or invalid_query" -q
```

Expected result: the new optional-evidence assertions fail against the current strict U1 behavior.

- [ ] **Step 5: Commit the red tests.**

```powershell
git add -- agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_r40_shadow_start.py
git commit -m "test: define research-permissive U1 evidence contract"
```

After the commit, dispatch a Luna high-reasoning review of the test-only diff. Do not implement production code until the reviewer reports no P0/P1 test-contract issue.

### Task 2: Implement optional-evidence U1 projection and research gate

**Files:**
- Modify: `agent/backtest/fund_rotation/pit_universe.py` in `_u1_snapshot()` and `_project_resolution_to_snapshot()`
- Test: `agent/tests/fund_rotation/test_pit_identity_layers.py`
- Test: `agent/tests/fund_rotation/test_runner_contract_integration.py`

**Interfaces:**
- Consumes: `PITUniverseSnapshot` from `_snapshot_from_resolution()`, existing identity keys, identity hash inputs, and `PITQualityStatus`.
- Produces: U1 snapshots whose eligible set equals U0, with `identity_validation_status`, `pit_evidence_status`, research/promotion/deployment diagnostics, and a projected resolution that can execute research-only U1 members.

- [ ] **Step 1: Add the smallest status helper or inline deterministic classification.**

Classify identity evidence from U0 eligible members without adding new schema types:

```text
no non-null identity key -> UNAVAILABLE
some but not all non-null identity keys -> PARTIAL
all non-null and any identity has more than one code -> CONFLICT
all non-null and no duplicate -> VERIFIED
```

Classify PIT evidence from the selected `FundInstrumentVersion` rows using existing `known_from`, `valid_from`, `revision_id`, `source_record_id`, and per-row quality state. Missing fields produce `UNAVAILABLE`/`PARTIAL`; they must not be synthesized.

- [ ] **Step 2: Make `_u1_snapshot()` preserve every U0 eligible member.**

For every code in `u0.eligible_codes`, emit `UniverseMembership(included=True, reason_code="U1_DERIVED_FROM_U0", identity_key=u0.identity_mapping.get(code), layer="U1")`. Preserve U0 exclusions unchanged as excluded U1 memberships. Set `eligible_codes=tuple(u0.eligible_codes)` and `u1_equals_u0=True`.

Retain duplicate/missing counts, representative mapping, identity hash, and deterministic fingerprint. Set research diagnostics from the status contract. Use `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` when optional identity/PIT evidence is unavailable, partial, or conflicting, unless U0 already has a core `PIT_INVALID` condition.

- [ ] **Step 3: Permit research-only U1 projection without weakening core failure.**

Change `_project_resolution_to_snapshot()` so it selects all included U1 members whenever U1 is research-only or another non-invalid status. It must still select no members for `PIT_INVALID`, and must carry U1 diagnostics and quality into the returned `UniverseResolution`.

- [ ] **Step 4: Run the focused green tests and regression tests.**

Run:

```powershell
python -m pytest agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py -q
python -m pytest agent/tests/fund_rotation/test_pit_universe.py agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py -q
```

Expected result: all tests pass, including deterministic hash, valid identity, missing identity, duplicate identity, injected resolver, and core fail-closed regressions.

- [ ] **Step 5: Commit the production projection.**

```powershell
git add -- agent/backtest/fund_rotation/pit_universe.py agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py
git commit -m "feat: allow research-only U1 without optional evidence"
```

Run the required Luna high-reasoning task review over the complete Task 2 diff; resolve every P0/P1 before Task 3.

### Task 3: Align serialization, research reports, and Shadow/promotion gates

**Files:**
- Modify: `experiments/fund_rotation_research_validity/pit_identity.py`
- Modify: `experiments/fund_rotation_research_validity/start_r40_shadow.py`
- Modify: `agent/tests/fund_rotation/test_pit_identity_layers.py`
- Modify: `agent/tests/fund_rotation/test_r40_shadow_start.py`
- Modify: `experiments/fund_rotation_research_validity/acceptance_matrix.md`

**Interfaces:**
- Consumes: `_snapshot_payload()` diagnostics from Task 2 and `_valid_u1_envelope()` input manifests.
- Produces: research manifests that remain available when optional evidence is absent, and qualification validators that reject unverified identity/PIT envelopes.

- [ ] **Step 1: Add failing serialization/report assertions.**

Update the existing PIT identity test to require the serialized U1 payload to include `identity_validation_status`, `pit_evidence_status`, `research_execution_allowed`, `promotion_allowed`, and `deployment_allowed`. Require the report to describe missing optional fields as research-only rather than unavailable solely for that reason.

- [ ] **Step 2: Add failing Shadow qualification assertions.**

Extend the canonical U0/U1 Shadow fixture with all five qualification fields and assert that:

```python
_valid_u1_envelope(unverified_same_set_payload) is False
_valid_u1_envelope(verified_same_set_payload) is True
```

The validator must continue checking hashes, fingerprints, membership, and equal sets in both cases.

- [ ] **Step 3: Update the serializer and research report.**

Because diagnostics are already part of `PITUniverseSnapshot.coverage_diagnostics`, pass them through unchanged in `_snapshot_payload()`. Change only the report wording and `snapshot_status` classification needed to distinguish `available_research_only` from core `invalid`; do not make up R39 returns or PIT evidence.

- [ ] **Step 4: Tighten `_valid_u1_envelope()` for qualification.**

Require `identity_validation_status == "VERIFIED"`, `pit_evidence_status == "VERIFIED"`, `promotion_allowed is True`, and `deployment_allowed is True` for Shadow/promotion acceptance. Continue requiring `u1_equals_u0 is True` and the exact deterministic snapshot hashes. Research manifests may be generated and run, but they must not pass this validator.

- [ ] **Step 5: Align the acceptance matrix and run focused tests.**

Update the Batch 1/Shadow rows to state: optional identity/PIT fields permit research-only execution; verified fields remain mandatory for Shadow/promotion. Run:

```powershell
python -m pytest agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_r40_shadow_start.py -q
```

- [ ] **Step 6: Commit and review.**

```powershell
git add -- experiments/fund_rotation_research_validity/pit_identity.py experiments/fund_rotation_research_validity/start_r40_shadow.py experiments/fund_rotation_research_validity/acceptance_matrix.md agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_r40_shadow_start.py
git commit -m "feat: separate research and U1 qualification gates"
```

Dispatch the required Luna high-reasoning review and do not proceed with real runs until there are no P0/P1 findings.

### Task 4: Verify the full research chain and produce evidence

**Files:**
- Modify only generated immutable experiment artifacts under `experiments/fund_rotation_research_validity/` when the existing scripts create them, plus the task ledger/report files required by the current workflow.
- Do not modify strategy, clustering, market-data schemas, or unrelated modules.

**Interfaces:**
- Consumes: the research-permissive U1 adapter and validators from Tasks 2–3, current Lance-backed market snapshots, and existing Batch 0–6 scripts.
- Produces: auditable manifests/reports that state whether each experiment ran, was unavailable for a core reason, or is research-only and not promotion-qualified.

- [ ] **Step 1: Run all focused implementation tests and static checks.**

```powershell
python -m pytest agent/tests/fund_rotation/test_pit_universe.py agent/tests/fund_rotation/test_pit_identity_layers.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_r40_shadow_start.py -q
git diff --check
git status --short --branch
```

- [ ] **Step 2: Re-run the real-data Batch 1 identity generator with the existing available inputs.**

If the current data source still lacks identity/PIT columns, the manifest must report `available_research_only` (or the existing equivalent), preserve the actual snapshot fingerprint, set the research flag true, and set promotion/deployment false. If required core inputs are missing, record `unavailable` with the exact missing-input reason; do not fabricate a completed experiment.

- [ ] **Step 3: Run the existing Batch 2–6 entry points in documented order.**

Use each script's existing CLI and input manifests. Keep each strategy ID and experiment boundary unchanged. For every run, verify manifest state, quality status, U0/U1 set equality, promotion/deployment flags, and that no missing optional field became a numeric value or a production qualification.

- [ ] **Step 4: Run the required whole-branch Luna review.**

Review the complete branch diff, all task reports, test output, and generated manifests. Any P0/P1 finding must be fixed in a dedicated reviewed fix step before declaring the contract complete.

- [ ] **Step 5: Record final status without overstating evidence.**

The final report must separately state: implementation status, tests passed, research runs completed, experiments unavailable due to core inputs, and the fact that missing optional identity/PIT fields still prevent Shadow/promotion/deployment qualification.
