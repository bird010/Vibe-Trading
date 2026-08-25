# R39 Incremental R57 Parameter Campaign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task with a fresh implementer and reviewer for each implementation task.

**Goal:** 按单变量 Champion–Challenger 实验，将 R57 的信号、代表生命周期和组合规则逐步融入 R39，并对每个阶段的基础版及有证据支持的调参版完成独立回测。

**Architecture:** 以 R39 为不可变 Champion，新增隔离策略包承载每个版本；每个版本只复用前一 Champion 的已冻结组件并替换一个主机制。所有版本通过统一 Catalog、Runner、PIT 数据、执行合同和 StrategyBatchService 运行，研究状态和逐版本决策写入独立实验目录与 append-only ledger。

**Tech Stack:** Python, Pydantic, pandas/numpy, pytest, Lance snapshots, StrategyBatchService, gpt-5.6-sol analyst, gpt-5.6-luna implementer/reviewer.

## Global Constraints

- 不修改既有 R39、R57 策略实现、默认值、公共 Runner、PIT/data contract、执行语义或评价政策。
- 每个 Challenger 使用唯一 `ai_rotation_rNN_<short-slug>` 策略 ID，并在 registry/catalog 精确列表中仅追加 ID。
- 每个版本只改变一个预注册参数或一个明确单变量机制；禁止宽范围参数搜索和 confirmation interval 选参。
- paired batch 必须使用 `schema_version="1"`、`mode="RESEARCH_ONLY"`、冻结执行对象和恰好 Champion + Challenger 两个 variants。
- Champion gates：Validation Sharpe 严格更高、年化收益不低、最大回撤恶化不超过 1 个百分点、有效 fold 中 Sharpe 多数获胜，且无 PIT、look-ahead、执行质量、完整性、可比性或实现门禁失败。
- 共享 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` 只允许研究排名，不能产生部署资格。
- 每轮严格执行 Sol 分析、Luna 实现、独立 Luna 审查、最多五轮修复/复审、测试、paired backtest、终态校验和 ledger 记录。
- 默认执行合同：初始资金 1000000.0、佣金 0.00025、最低佣金 5.0、其他费用 0.0、参与率 0.05、ADV20、最少 10 个观测、滑点 5–30 bps、整手 100。

## Task 1: Freeze campaign state and preflight evidence

**Files:**
- Create: `.superpowers/sdd/r39-incremental-r57-parameter-campaign/progress.md`
- Create: `experiments/ai_fund_rotation_r39_incremental_r57_20260825/experiment_spec.json`
- Create: `experiments/ai_fund_rotation_r39_incremental_r57_20260825/fold_manifest.json`
- Create: `experiments/ai_fund_rotation_r39_incremental_r57_20260825/champion.json`
- Create: `experiments/ai_fund_rotation_r39_incremental_r57_20260825/experiment_ledger.jsonl`

**Interfaces:** Resolve the previous paired R39/R57 batch, current catalog identities, earliest common PIT-valid date, frozen snapshot, research-selection interval, three rolling validation folds, and execution identity hashes. Record the prior R57 full-bundle result as exploratory evidence only.

- [ ] Verify the prior R39/R57 batch is terminal, comparable, and not treated as untouched OOS.
- [ ] Resolve `fund`, `fact_fund_adj`, and `dim_fund` common PIT-valid coverage without shortening the 156-week train / 52-week validation policy.
- [ ] Freeze Champion R39 artifacts, data snapshot, calendar, benchmark, framework, execution contract and gates.
- [ ] Append the preflight result and any data-gap finding to the ledger.

## Task 2: Round 01 signal-only baseline

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r58_r39_signal_r57/`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r58_r39_signal_r57.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py` only to append the new strategy.
- Modify: exact Catalog/API tests only to append the new ID.
- Create: `experiments/ai_fund_rotation_r39_incremental_r57_20260825/rounds/round_01_baseline/`

**Interfaces:** Keep R39 representative lifecycle and incumbent-carry portfolio behavior. Replace only the signal computation with the R57 three-factor signal while preserving causal availability, R39 target-count/weight semantics, cash conservation, and execution contracts.

- [ ] Sol analyst writes one falsifiable signal hypothesis and design.
- [ ] Luna implementer writes behavior tests first, verifies failure, adds isolated strategy, and runs focused/regression tests.
- [ ] Fresh Luna reviewer checks scope, causal timing, missing data, formulas, determinism, and R39 isolation.
- [ ] Controller submits exact Champion + baseline paired batch and verifies terminal manifests/checksums.
- [ ] Record fold metrics and Champion decision; retain R39 if any gate fails.

## Task 3: Round 01 evidence-based tuning

**Files:**
- Create one new isolated strategy package per justified tuning candidate under `agent/backtest/fund_rotation/strategies/`.
- Create one focused test file and one evidence directory per candidate under `agent/tests/fund_rotation/` and `experiments/.../rounds/`.
- Modify registry/catalog/API exact lists only by appending each new ID.

**Interfaces:** Start from the current Round 01 Champion. Propose at most three candidates, each changing exactly one pre-registered signal parameter or single signal mechanism supported by fold/regime/holdings/cost evidence. Do not create tune2/tune3 without a distinct falsifiable hypothesis.

- [ ] For each candidate, repeat Sol design, Luna implementation, fresh review and paired batch validation.
- [ ] Consume and ledger every candidate outcome, including no-improvement or technical failure.
- [ ] Promote only a candidate passing every frozen gate; otherwise preserve the current Champion before Round 02.

## Task 4: Round 02 representative-lifecycle baseline

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_rNN_r57_rep_lifecycle/`
- Create: `agent/tests/fund_rotation/test_ai_rotation_rNN_r57_rep_lifecycle.py`
- Modify: registry and exact Catalog/API lists only to append the new ID.
- Create: `experiments/.../rounds/round_02_baseline/`

**Interfaces:** Build on the selected Round 01 Champion. Replace only representative locking, 26-week reclustering and hard-invalid replacement with the R57 lifecycle; preserve the selected signal and R39 portfolio rules.

- [ ] Complete the full analyst → implementer → reviewer → test → paired batch → gate loop.
- [ ] Retain R39 or the current Round 01 Champion when any gate fails.

## Task 5: Round 02 evidence-based tuning

**Files:**
- Create one isolated strategy package and focused test file per justified candidate.
- Create one ledger/artifact directory per candidate.
- Append only new IDs to registry/catalog/API exact assertions.

**Interfaces:** Tune one lifecycle parameter or mechanism at a time from Round 02 fold and execution evidence; at most three candidates, no forced candidates.

- [ ] Execute and record each candidate through the same five-gate workflow.
- [ ] Freeze the best passing Round 02 Champion before Round 03.

## Task 6: Round 03 portfolio-rule baseline

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_rNN_r57_portfolio_rules/`
- Create: `agent/tests/fund_rotation/test_ai_rotation_rNN_r57_portfolio_rules.py`
- Modify: registry and exact Catalog/API lists only to append the new ID.
- Create: `experiments/.../rounds/round_03_baseline/`

**Interfaces:** Build on the selected Round 02 Champion. Replace only R39 Top-3/staged-entry/incumbent-carry portfolio behavior with R57 Top-1/full-target/strict-threshold switching; preserve the selected signal and representative lifecycle.

- [ ] Complete analyst, implementation, review, tests, paired batch and frozen gate.
- [ ] Keep the preceding Champion if the portfolio replacement fails.

## Task 7: Round 03 evidence-based tuning

**Files:**
- Create one isolated strategy package and focused test file per justified candidate.
- Create one ledger/artifact directory per candidate.
- Append only new IDs to registry/catalog/API exact assertions.

**Interfaces:** Tune one portfolio parameter or single portfolio mechanism at a time from Round 03 evidence; at most three candidates, no confirmation-data selection.

- [ ] Execute each justified candidate through the complete review and paired-validation gates.
- [ ] Freeze the final research Champion and retain all rejected candidates in the ledger.

## Task 8: Whole-campaign verification and final research freeze

**Files:**
- Create: `experiments/ai_fund_rotation_r39_incremental_r57_20260825/final_candidate.json`
- Create: `experiments/.../confirmation_report.md`
- Create: `experiments/.../forward_shadow_spec.json`

- [ ] Run the full relevant fund-rotation regression suite and verify catalog invariants.
- [ ] Validate every batch manifest, child state, checksum, snapshot/framework/execution identity and fold comparison.
- [ ] Produce Champion path, all version decisions, multiple-testing risk, unresolved uncertainty and research-only limitations.
- [ ] Freeze the final candidate as `FROZEN_RESEARCH_CANDIDATE`; pre-register at least 104 weeks of forward shadow and do not claim deployment qualification.
