# R39 Rank Buffer Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement and validate an isolated R39 Top3-entry/Top4-exit challenger and run its auditable Champion comparison.

**Architecture:** Add a new strategy package that subclasses the existing R39 session and replaces only cluster selection with epoch-scoped rank hysteresis. Register the new descriptor without changing existing strategies, then submit a paired R39/R67 research batch using the frozen experiment contract and persist all artifacts under a new experiment directory.

**Tech Stack:** Python 3, pytest, Pydantic strategy contracts, existing fund-rotation registry and StrategyBatch API, JSON/JSONL experiment artifacts.

## Global Constraints

- New strategy ID: `ai_rotation_r67_r39_rank_buffer`.
- Existing strategy implementations/defaults, public Runner, PIT/data contract, execution semantics, and evaluation policy remain unchanged.
- Entry rank is Top3; same-epoch exit rank is Top4; reclustering resets prior selection.
- Use the frozen execution object: initial capital 1000000.0, commission rate 0.00025, commission minimum 5.0, other fee rate 0.0, max participation rate 0.05, ADV lookback 20, ADV minimum observations 10, base slippage 5.0 bps, max slippage 30.0 bps, lot size 100.
- Selection interval is `20160701..20220729`; confirmation interval `20220801..20260801` is excluded from selection.
- `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` is rankable only when Champion and Challenger share the same snapshot/universe status and comparability flags; no deployment qualification may be claimed.

### Task 1: Implement R67 rank-buffer challenger

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r67_r39_rank_buffer/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r67_r39_rank_buffer/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r67_r39_rank_buffer.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py` (append the new descriptor/strategy registration)
- Modify: `agent/tests/fund_rotation/test_strategy_catalog.py` (append only the new exact catalog ID)
- Create: `experiments/ai_fund_rotation_r67_rank_buffer_20260829/experiment_spec.json`
- Create: `experiments/ai_fund_rotation_r67_rank_buffer_20260829/rounds/round_01/analysis.md`
- Create: `experiments/ai_fund_rotation_r67_rank_buffer_20260829/rounds/round_01/design.md`

**Interfaces:**
- Reuse `AiRotationR39IncumbentCarrySession.evaluate`, `apply_incumbent_carry`, and the existing representative/session artifacts.
- Add a pure helper `select_rank_buffer_clusters(ranked_clusters: Sequence[int], previous_selected: Sequence[int], top_n: int = 3, exit_rank: int = 4, epoch_reset: bool = False) -> tuple[list[int], list[int]]`.
- The helper returns selected cluster IDs and retained prior cluster IDs; it must retain only prior IDs present in the current ranking with rank <= `exit_rank`, fill remaining slots from current ranking order, and return at most `top_n` IDs.

- [ ] **Step 1: Write the failing behavior tests**

Add tests for: rank-4 prior selection is retained; rank-5 prior selection is forced out and a current candidate fills the slot; epoch reset ignores prior selection; selection is deterministic and bounded to Top3.

- [ ] **Step 2: Run the new tests and verify the expected failure**

Run `python -m pytest agent/tests/fund_rotation/test_ai_rotation_r67_r39_rank_buffer.py -q` from the repository root. Expected: collection/import failure because the new package and helper do not yet exist.

- [ ] **Step 3: Implement the minimal isolated strategy**

Copy the smallest applicable R63 rank-buffer pattern, but subclass the R39 session. Keep R39's call to `super().evaluate(context)` for clustering, representative selection, staging, and incumbent carry; use the R39 diagnostics' ranked clusters and recluster flag only to select clusters, then rebuild the target slots and patch the final R39 weights. Store previous selected clusters on the new session only. Include diagnostics for `rank_buffer`, `entry_rank`, `exit_rank`, retained clusters, forced exits, and epoch reset. Do not alter R39 source or shared execution code.

- [ ] **Step 4: Register only the new strategy**

Append the import and registry entry in the same style as R66. Append `ai_rotation_r67_r39_rank_buffer` to the existing exact catalog expectation without removing or weakening any prior assertion.

- [ ] **Step 5: Run focused and regression tests**

Run `python -m pytest agent/tests/fund_rotation/test_ai_rotation_r67_r39_rank_buffer.py agent/tests/fund_rotation/test_ai_rotation_r39_incumbent_carry.py agent/tests/fund_rotation/test_strategy_catalog.py -q`, then `python -m pytest agent/tests/fund_rotation -q`. Record exact output and any environment failures in the round report.

- [ ] **Step 6: Run the paired research batch**

Create one `schema_version=1`, `mode=RESEARCH_ONLY` StrategyBatch request containing exactly R39 and R67, the frozen execution object, the frozen selection interval/folds, and a stable idempotency key. POST to `/stockpred/fund-rotation/strategy-batches`, poll the returned batch ID to a documented terminal state, validate child manifests/checksums, snapshot/framework/execution identities, quality/comparability flags, fold completeness, and metric identities. Persist the original request and terminal result under `experiments/ai_fund_rotation_r67_rank_buffer_20260829/rounds/round_01/`.

- [ ] **Step 7: Apply the Champion gates and persist the ledger**

Write the fold-level comparison and decision: aggregate validation Sharpe strictly higher, annual return not lower, drawdown degradation <= 1 percentage point, Sharpe wins in > half valid folds, and no technical/comparability gate failure. Update Champion only if all gates pass; otherwise retain R39. Append a terminal decision to `experiment_ledger.jsonl`, and create `champion.json`, `final_candidate.json`, `confirmation_report.md`, and `forward_shadow_spec.json` with research-only status.

- [ ] **Step 8: Verify completion claims**

Run `git diff --check`, rerun the focused tests, verify all required experiment artifact paths exist and parse as JSON where applicable, and report the actual terminal batch/decision status. Do not claim deployment qualification.
