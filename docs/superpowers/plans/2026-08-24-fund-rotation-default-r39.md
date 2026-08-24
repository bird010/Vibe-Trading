# Fund Rotation R39 Default Implementation Plan

> **For agentic workers:** Execute this plan task-by-task in the current session. Preserve unrelated working-tree changes.

**Goal:** 将基金轮动前端新建回测的默认策略切换为 R39，并保留 R11 兜底。

**Architecture:** 默认策略由 `FundRotationTab.tsx` 的常量控制；策略目录仍由后端动态提供。仅改变前端初始选择，不改变策略算法、后端目录或执行链路。

**Tech Stack:** React、TypeScript、Vitest、Vite。

## Global Constraints

- 只修改默认策略和对应前端测试。
- 保留 `ai_rotation_r11_persist_geom` 作为 fallback。
- 不覆盖工作区已有的 R35–R56 未提交研究改动。

---

### Task 1: Update the default strategy contract

**Files:**
- Modify: `frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx`

**Interfaces:**
- The component continues to consume the backend strategy catalog.
- The default selection must be `ai_rotation_r39_incumbent_carry`.
- The fallback selection remains `ai_rotation_r11_persist_geom`.

- [ ] **Step 1: Write the failing test**

Change the existing default-selection test description and expected value from R34 to R39:

```tsx
it("uses the requested default dates and r39 strategy", () => {
  render(<FundRotationTab />);

  expect(screen.getByLabelText("开始日期")).toHaveValue("2022-08-01");
  expect(screen.getByLabelText("结束日期")).toHaveValue("2026-08-01");
  expect(screen.getAllByRole("combobox")[0]).toHaveValue(
    "ai_rotation_r39_incumbent_carry",
  );
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run from `frontend`:

```powershell
npm test -- --run src/components/stockpred/__tests__/FundRotationTab.test.tsx
```

Expected: the default strategy assertion fails because the implementation still initializes R34.

- [ ] **Step 3: Make the minimal implementation change**

In `FundRotationTab.tsx`, change only:

```ts
const DEFAULT_STRATEGY_ID = "ai_rotation_r39_incumbent_carry";
```

Leave this unchanged:

```ts
const FALLBACK_STRATEGY_ID = "ai_rotation_r11_persist_geom";
```

- [ ] **Step 4: Run the focused test and verify it passes**

Run the same focused Vitest command and expect zero failures.

- [ ] **Step 5: Run the frontend regression and build**

Run from `frontend`:

```powershell
npm test -- --run
npm run build
```

Expected: both commands exit with code 0.
