# 基金轮动策略批次默认配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让基金轮动策略批次配置页面默认使用指定回测区间和相关性代表 ETF 策略。

**Architecture:** 在现有 `FundRotationTab` 的本地表单状态初始化逻辑中设置公共日期，并在 Catalog 策略列表加载后按稳定的策略 ID 优先选择相关性代表策略。保留首项回退，避免 Catalog 不含目标策略时破坏现有页面。

**Tech Stack:** React、TypeScript、Vitest、Testing Library、Vite。

## Global Constraints

- 策略专用参数必须继续由 Catalog 的 `default_config` 驱动。
- 日期仍以 HTML `input[type=date]` 的 `YYYY-MM-DD` 值保存，并在提交时沿用现有格式转换。
- 不修改后端 API 契约或其他页面行为。

### Task 1: 用测试锁定默认配置行为

**Files:**
- Modify: `frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx`

**Interfaces:**
- Consumes: mocked `useFundRotation` Catalog state.
- Produces: a batch form whose date inputs default to `2022-08-01` and `2026-08-01`, and whose first variant prefers `correlation_representative`.

- [ ] **Step 1: Write the failing tests**

  Extend the page test mock with a `correlation_representative` strategy detail and add assertions using the existing date input labels and strategy select.

- [ ] **Step 2: Run the focused tests and verify they fail**

  Run from `frontend`:

  ```powershell
  npm test -- --run src/components/stockpred/__tests__/FundRotationTab.test.tsx
  ```

  Expected: the new assertions fail because dates are currently empty and the first strategy is the first Catalog item.

- [ ] **Step 3: Implement the minimal behavior**

  Initialize:

  ```ts
  const [startDate, setStartDate] = useState("2022-08-01");
  const [endDate, setEndDate] = useState("2026-08-01");
  ```

  In the existing Catalog initialization effect, choose:

  ```ts
  const strategy =
    strategies.find((item) => item.strategy_id === "correlation_representative") ??
    strategies[0];
  ```

  Keep the existing `default_config` merge and effect guard unchanged.

- [ ] **Step 4: Run the focused tests and verify they pass**

  Run the same Vitest command and confirm the new assertions pass.

- [ ] **Step 5: Run the full frontend verification**

  ```powershell
  npm test -- --run
  npm run build
  ```

  Expected: Vitest exits successfully and the production build exits with code 0.

- [ ] **Step 6: Review the diff**

  ```powershell
  git diff --check
  git diff -- frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx
  ```

  Confirm only the requested defaults, fallback selection, and regression assertions changed.
