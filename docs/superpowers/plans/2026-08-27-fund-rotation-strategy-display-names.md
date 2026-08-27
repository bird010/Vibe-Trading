# 基金轮动策略展示名称统一 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让基金轮动前端展示的每个 Rxx 策略名称都以自身真实策略代号开头。

**Architecture:** 在基金轮动 Catalog 这一现有展示数据边界统一生成名称，避免逐个前端组件修补。策略 ID、策略逻辑、描述和历史运行数据保持不变；列表和详情 API 均继续读取 Catalog 注册后的 descriptor。

**Tech Stack:** Python、pytest、FastAPI 现有基金轮动 Catalog/API。

## Global Constraints

- 只影响基金轮动策略的前端展示名称。
- 每个 `ai_rotation_rXX_*` 的名称必须以 `RXX` 开头。
- 不修改策略 ID、策略实现、策略描述、配置或历史运行记录。

---

### Task 1: 建立展示名称回归约束

**Files:**
- Modify: `agent/tests/fund_rotation/test_strategy_catalog.py`

- [ ] **Step 1: Write the failing test**

在 `TestCatalogRegistration` 中增加测试，遍历默认 Catalog，并断言每个 `ai_rotation_rXX_` 条目的名称以从自身 ID 提取出的 `RXX` 开头。

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest agent/tests/fund_rotation/test_strategy_catalog.py -q`

Expected: FAIL，至少 R05、R64 等条目的名称没有以自身真实代号开头。

### Task 2: 在 Catalog/API 边界统一名称

**Files:**
- Modify: `agent/backtest/fund_rotation/catalog.py`

- [ ] **Step 1: Implement the minimal normalization**

增加仅针对 `ai_rotation_rXX_` ID 的规范化逻辑：保留原名称正文，在名称前补上自身真实代号；若已经以自身代号开头则保持不变。Catalog 注册后的 descriptor 被列表和策略详情 API 共同使用。

- [ ] **Step 2: Run focused tests**

Run: `pytest agent/tests/fund_rotation/test_strategy_catalog.py -q`

Expected: PASS。

- [ ] **Step 3: Run related regression tests**

Run: `pytest agent/tests/fund_rotation/test_strategy_catalog.py agent/tests/stockpred/test_strategy_detail.py -q`

Expected: PASS，且无名称相关回归。

- [ ] **Step 4: Review the diff**

Run: `git diff --check; git diff --stat`

Expected: 仅包含名称规范化、对应测试和本计划文档。
