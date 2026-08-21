# Fund Rotation R11 Champion Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不修改 R11、Round 01–30、公共 Runner 或 promotion 契约的前提下，新增可审计、可恢复的 R11 Champion Validation 子域，并覆盖冻结契约、Universe/基准、机制诊断、稳健性、归因、统计与中文最终报告。

**Architecture:** 新增独立 `champion_validation` 包。契约模块负责冻结身份、实验规格、哈希与阶段状态；各验证模块只产生结构化结果，不注册诊断变体或选择新 Champion；controller 负责按门禁顺序运行、幂等恢复、追加 ledger 和最终决策。复用现有 PIT、benchmark、execution、metrics、attribution 与 robustness 能力，不改其公共语义。

**Tech Stack:** Python 3.11+, dataclasses/Enum, pandas, hashlib/json, pytest；沿用现有 fund_rotation contracts、PIT、benchmark、execution 和 metrics 模块。

## Global Constraints

- 不修改 R11 源码、默认参数、策略身份、Round 01–30 工件或现有 promotion gate。
- 不创建 Round 31，不根据参数网格选择 Champion 或推荐参数。
- consumed confirmation interval `2022-08-01..2026-08-01` 不得用于设计、选择或阈值拟合。
- trial count 固定为 30；缺失、空样本、非有限指标不能默认填 0。
- 所有结构化工件必须包含 schema_version、experiment_id、timestamp、输入/数据/框架/策略/执行/spec 哈希、状态和 reason codes。
- 最终报告使用中文；默认最终动作是 `FORWARD_SHADOW_ONLY`，验证软件成功不等于研究通过。

---

### Task 1: Validation contracts, frozen specs, artifacts, and decision state machine

**Files:**
- Create: `agent/backtest/fund_rotation/champion_validation/__init__.py`
- Create: `agent/backtest/fund_rotation/champion_validation/contracts.py`
- Create: `agent/backtest/fund_rotation/champion_validation/decision.py`
- Create: `agent/backtest/fund_rotation/champion_validation/report.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_contracts.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_decision.py`

**Interfaces:**
- Produces immutable experiment constants, `ValidationContract`, `StageResult`, `ValidationLedger`, `ValidationDecision`, `freeze_identity()`, `validate_confirmation_interval()`, `append_ledger_entry()`, and `evaluate_final_decision()`.
- `ValidationContract` exposes `experiment_id`, `experiment_type`, `subject_strategy`, `candidate_selection_enabled=False`, `promotion_enabled=False`, `subject_status`, `trial_count=30`, research dates, consumed interval, thresholds, and all pre-registered matrices.

- [ ] **Step 1: Write failing tests** for frozen constants, deterministic canonical hashes, rejection of confirmation-interval overlap, schema fields, append-only ledger entries, and PASS/INCONCLUSIVE/FAIL propagation into the three allowed final actions.
- [ ] **Step 2: Run** `pytest agent/tests/fund_rotation/champion_validation/test_contracts.py agent/tests/fund_rotation/champion_validation/test_decision.py -q` and confirm failure.
- [ ] **Step 3: Implement** only the contract/state/report primitives with deterministic JSON serialization and no strategy catalog registration.
- [ ] **Step 4: Run the focused tests** and then `pytest agent/tests/fund_rotation/test_pit_universe.py agent/tests/fund_rotation/test_benchmarks.py -q` to verify no existing contract regression.
- [ ] **Step 5: Commit** only the new champion-validation files and tests.

### Task 2: Universe assurance, benchmark suite, and behavior comparison

**Files:**
- Create: `agent/backtest/fund_rotation/champion_validation/universe_assurance.py`
- Create: `agent/backtest/fund_rotation/champion_validation/benchmark_suite.py`
- Create: `agent/backtest/fund_rotation/champion_validation/behavior_comparison.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_universe_assurance.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_benchmark_suite.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_behavior_comparison.py`

**Interfaces:**
- `assure_universe(resolutions, expected_quality=...) -> UniverseAssuranceResult` maps existing PIT diagnostics to the exact zero-count gates and returns `VERIFIED`/`INCONCLUSIVE_UNIVERSE` without weakening missing-data rules.
- `build_benchmark_specs() -> tuple[BenchmarkSpec, ...]` returns B0–B5 with frozen behavior and comparability metadata; `compare_execution_identity()` rejects mismatched snapshots/universe/calendar/cost/execution identities.
- `compare_behavior(reference, candidate, tolerance) -> BehaviorComparison` computes eligibility/ranking/selection/weight/trade/cash difference ratios and marks behavioral equivalence.

- [ ] **Step 1: Write failing fixture tests** covering cross-source conflicts, lifecycle leakage, missing knowledge time, B0–B5 definitions, theoretical benchmark isolation, and same-path zero differences.
- [ ] **Step 2: Run focused tests** and verify they fail.
- [ ] **Step 3: Implement adapters around existing PIT/benchmark/execution modules; do not alter those modules.** Preserve benchmark unavailable as an explicit non-comparable result.
- [ ] **Step 4: Run focused tests plus existing PIT/benchmark/attribution tests.**
- [ ] **Step 5: Commit the isolated component files and tests.**

### Task 3: Diagnostic variants, stability surface, and stress scenarios

**Files:**
- Create: `agent/backtest/fund_rotation/champion_validation/diagnostic_variants.py`
- Create: `agent/backtest/fund_rotation/champion_validation/stability_surface.py`
- Create: `agent/backtest/fund_rotation/champion_validation/stress_tests.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_diagnostic_variants.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_stability_surface.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_stress_tests.py`

**Interfaces:**
- `build_ablation_matrix()` returns A–E only as controller diagnostics plus frozen R11 parity metadata; no catalog entries.
- `build_stability_grid()` returns exactly 45 points from windows `[3,4,6,8,12]`, top_n `[2,3,4]`, recluster `[13,26,52]`; `evaluate_stability_surface(results)` applies all gates and never emits winner/recommended fields.
- `build_stress_scenarios()` returns the pre-registered one-factor scenarios; `evaluate_stress_results()` applies 20 bps, one-day delay, and 1% ADV gates and reports break-even cost without tuning.

- [ ] **Step 1: Write failing tests** for A–E single-difference declarations, E/R11 parity, 45-point completeness, parameter-island rejection, all stress dimensions, and technical-failure blocking.
- [ ] **Step 2: Run focused tests to verify failure.**
- [ ] **Step 3: Implement pure spec builders/evaluators using existing strategy and execution interfaces.**
- [ ] **Step 4: Run focused tests and existing robustness tests.**
- [ ] **Step 5: Commit isolated files and tests.**

### Task 4: Regime/concentration attribution and statistical validation

**Files:**
- Create: `agent/backtest/fund_rotation/champion_validation/regime_attribution.py`
- Create: `agent/backtest/fund_rotation/champion_validation/statistical_validation.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_regime_attribution.py`
- Test: `agent/tests/fund_rotation/champion_validation/test_statistical_validation.py`

**Interfaces:**
- `classify_regimes(features, train_masks) -> tuple[RegimeLabel, ...]` uses only decision-time-known features and fold-train-fitted thresholds; `compute_regime_and_concentration(...)` returns the required regime/ETF/year/fold/cluster fields and the INCONCLUSIVE concentration rules.
- `time_block_bootstrap(..., samples=10000, seed=...)` preserves time blocks and is reproducible; `compute_deflated_sharpe_ratio(...)`; `run_reality_check_or_spa(...)` consumes all valid candidate series and fixed trial count; `validate_statistics(...)` returns PASS/INCONCLUSIVE/FAIL with CI, DSR, p-value and reason codes.

- [ ] **Step 1: Write failing tests** for causal regime labels, concentration caps, bootstrap reproducibility, 10,000-sample defaults, DSR inputs, trial-count handling, invalid series exclusion, and three-state outcomes.
- [ ] **Step 2: Run focused tests to verify failure.**
- [ ] **Step 3: Implement by extending/wrapping existing `robustness.py` and `attribution.py` primitives; retain serializable compressed/bootstrap evidence.**
- [ ] **Step 4: Run focused tests and existing attribution/robustness tests.**
- [ ] **Step 5: Commit isolated files and tests.**

### Task 5: Controller, recovery/idempotency, CLI, and Chinese report

**Files:**
- Create: `agent/backtest/fund_rotation/champion_validation/controller.py`
- Create: `agent/scripts/run_fund_rotation_champion_validation.py`
- Create: `agent/tests/fund_rotation/champion_validation/test_controller.py`
- Create: `agent/tests/fund_rotation/champion_validation/test_cli.py`

**Interfaces:**
- `ChampionValidationController.run(...) -> ValidationRunResult` executes preflight → universe → benchmarks → ablation → stability → stress → attribution → statistics → final in order, stops interpretation after failed prerequisites, writes the exact experiment directory tree and append-only ledger, and resumes only after rechecking hashes.
- CLI accepts `--experiment-dir`, `--contract`, `--resume`, `--idempotency-key`; writes Chinese `report.md` and JSON artifacts; HTTP/SSE/background-process style partial states are never treated as completion.

- [ ] **Step 1: Write failing integration tests** for full PASS fixture, universe gap stop, identity drift block, partial/corrupt artifact recovery, idempotent rerun, and final report exclusion of winner/recommended fields.
- [ ] **Step 2: Run focused tests to verify failure.**
- [ ] **Step 3: Implement controller orchestration and CLI around the component interfaces; keep stage outputs deterministic and all writes within the new experiment directory.**
- [ ] **Step 4: Run the full champion-validation suite and the fund-rotation regression suite.**
- [ ] **Step 5: Commit the controller, CLI, tests, and only newly generated validation artifacts.**

### Task 6: Whole-branch safety review and verification

**Files:**
- Modify only if required by tests: new champion-validation files above.

- [ ] **Step 1:** Compare `git status` and `git diff --name-only` against the baseline; confirm no Round 05–30, R11, registry, public runner, or existing experiment ledger files changed.
- [ ] **Step 2:** Run `pytest agent/tests/fund_rotation/champion_validation -q`.
- [ ] **Step 3:** Run relevant regression tests: `pytest agent/tests/fund_rotation/test_pit_universe.py agent/tests/fund_rotation/test_benchmarks.py agent/tests/fund_rotation/test_attribution.py agent/tests/fund_rotation/test_robustness.py -q`.
- [ ] **Step 4:** Run static import/compile checks for the new package and CLI.
- [ ] **Step 5:** Record any environment-blocked tests explicitly; do not claim research PASS from software tests.

