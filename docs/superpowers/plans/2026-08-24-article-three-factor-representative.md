# 文章三因子代表基金策略实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 在不改变既有策略或公共执行/数据契约的前提下，实现预注册的 R57 文章三因子代表基金轮动策略。

**架构：** 新增隔离的 R57 策略包，包含冻结配置、因子/评分/阈值纯函数，以及复用现有相关性代表基金生命周期的日频 Session；Session 自己维护 ISO 周重聚类状态。只在显式 registry 和 Catalog/API 精确测试中追加 R57，保持 R34、R39 与前端默认不变。

**技术栈：** Python 3、pandas、Pydantic、现有基金轮动契约、pytest。

## 全局约束

- Strategy ID is `ai_rotation_r57_three_factor_representative`.
- Fixed article parameters are bias/slope/efficiency windows `25`, weights `0.3/0.3/0.4`, threshold `1.5`, target weight `1.0`, minimum complete candidates `2`, Z-Score `ddof=0`, and daily frequency `D`.
- Candidate assets are current locked correlation-cluster representatives; clustering and representative locks retain existing 26-week lifecycle semantics.
- Signal data is causal through signal-date close, with PIT universe, daily OHLC, amount, and adjustment factors; execution remains in the public Runner.
- Do not modify R34, R39, existing representative behavior, the public Runner, CausalDataView, execution contracts, API routes, or frontend defaults.
- Missing/non-finite data fails closed; no zero fill, stale-factor reuse, cross-asset imputation, same-close execution, or future-data access.
- Strict JSON diagnostics must contain `null` rather than NaN/Infinity and must include factor scores, threshold fields, and `negative_threshold_case` when applicable.

### Task 1: Implement and test the isolated R57 strategy

**Files:**
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/__init__.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/config.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/factors.py`
- Create: `agent/backtest/fund_rotation/strategies/ai_rotation_r57_three_factor_representative/strategy.py`
- Create: `agent/tests/fund_rotation/test_ai_rotation_r57_three_factor_representative.py`
- Modify: `agent/backtest/fund_rotation/strategies/registry.py`
- Modify: `agent/tests/fund_rotation/test_strategy_catalog.py`
- Modify: `agent/tests/fund_rotation/test_fund_rotation_catalog_api.py`

**Interfaces:**
- The strategy exposes `AiRotationR57ThreeFactorRepresentativeStrategy`, `ArticleThreeFactorRepresentativeConfig`, and the package-private pure functions specified in the design document.
- `create_session()` returns an isolated Session implementing `scheduled_dates()`, `evaluate()`, and `finalize()` through existing fund-rotation contracts.
- Registry and exact catalog/API lists retain every existing strategy ID and append only R57.

- [ ] Read the design specification and existing correlation-representative/R39 implementations; preserve all unrelated dirty-worktree changes.
- [ ] Write focused tests for adjustment, all three factor formulas, invalid/constant data, same-complete-set Z-Scores, deterministic ranking, threshold equality/negative cases, daily scheduling, causal access, representative lifecycle, fallback behavior, diagnostics, and weight/cash conservation.
- [ ] Implement the frozen Pydantic config with fixed-value validation and `extra="forbid"`.
- [ ] Implement the exact pure-function signatures from the specification, including adjusted OHLC and fail-closed finite-value handling.
- [ ] Implement the R57 descriptor, requirements, pipeline description, daily Session, independent ISO-week reclustering counter, representative lock reuse, Top-1 threshold state, decisions, and strict diagnostics.
- [ ] Append R57 to the explicit registry and exact catalog/API test lists without altering existing IDs.
- [ ] Run the focused R57 tests, catalog tests, and relevant existing representative/R39 tests; fix failures within the stated scope.
- [ ] Run `git diff --check` and report changed paths, test commands, and any unresolved concern. Do not reset, clean, or overwrite unrelated work.

### Review iteration protocol

- [ ] A separate 5.6 Luna review agent checks the implementation against the full specification and the actual diff, with separate verdicts for spec compliance, causal correctness, contract safety, test adequacy, and scope hygiene.
- [ ] If review finds issues, the implementation agent makes only scoped fixes, then the review agent rechecks the changed areas.
- [ ] Repeat implementation/review until the review is clean or a genuine specification blocker is identified.

### Verification

- [ ] Main agent reruns focused R57/catalog tests and relevant existing strategy tests after the review loop.
- [ ] Main agent reports exact pass/fail evidence and does not claim broader success if unrelated failures remain.
