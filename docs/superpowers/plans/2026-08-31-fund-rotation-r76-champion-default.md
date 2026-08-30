# R76 固定短债 Champion 与策略批次默认选择 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `ai_rotation_r76_fixed_short_bond` 记录为当前研究 Champion，并将前端策略批次配置的默认选择改为 R76。

**Architecture:** 新增一个 append-only 研究 Champion 指针，引用已完成的 2015–2022 batch/run 证据，不覆盖历史 campaign 文件。前端只修改新建批次的初始策略常量，继续使用后端动态目录和 R11 fallback；不修改策略算法、后端 Runner 或生产晋级门禁。

**Tech Stack:** Python JSON artifact、React、TypeScript、Vitest、Vite、pytest。

## Global Constraints

- Champion 状态必须是 `FROZEN_RESEARCH_CANDIDATE`，不得写成生产 Champion。
- R76 证据必须来自 batch `bc2c1d09c759`、run `b445a3424213`、snapshot `2ce471286eaa99c2f2159c30b9c987fb0bd1972155da0a2b99087f2b1ae8ecae`。
- 有效区间必须保持 `20150105..20221230`，质量状态保持 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`。
- `promotion_allowed` 和 `deployment_allowed` 必须保持 `false`。
- 前端必须保留 `ai_rotation_r11_persist_geom` 作为 fallback。
- 不修改既有策略实现、公共 Runner、execution ledger、PIT/data contract 或历史实验产物。
- 每个任务完成后，使用已有 5.6 Luna reviewer；P0/P1 任一大于 0 时停止并修复后重新 review。

## File Map

- Create: `experiments/fund_rotation_research_validity/champion.json` — 当前研究 Champion 的可追踪指针和冻结指标。
- Modify: `frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx:23-24` — 新建策略批次的默认选择和 fallback 常量。
- Modify: `frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx:259-267` — 默认策略回归断言。

### Task 1: Create the R76 research Champion pointer

**Files:**
- Create: `experiments/fund_rotation_research_validity/champion.json`
- Test: inline JSON consistency check against `C:\Users\LK\.codex\visualizations\2026\08\29\01a04ca0-95f0-7cc1-b3eb-f1f868600e0e\real-chain-2015-2022-final\research_chain_manifest.json`

**Interfaces:**
- Consumes: the completed R76 evidence identified by batch/run/snapshot in the Global Constraints.
- Produces: a JSON pointer with `strategy_id`, `status`, `source`, `evaluation_range`, `metrics`, `quality_status`, `promotion_allowed`, and `deployment_allowed`.

- [ ] **Step 1: Write the exact Champion pointer artifact**

Create `experiments/fund_rotation_research_validity/champion.json` with this content:

```json
{
  "schema_version": "1",
  "status": "FROZEN_RESEARCH_CANDIDATE",
  "strategy_id": "ai_rotation_r76_fixed_short_bond",
  "source": {
    "batch_id": "bc2c1d09c759",
    "run_id": "b445a3424213",
    "snapshot_fingerprint": "2ce471286eaa99c2f2159c30b9c987fb0bd1972155da0a2b99087f2b1ae8ecae",
    "evaluation_start_date": "20150105",
    "evaluation_end_date": "20221230"
  },
  "quality_status": "RESEARCH_ONLY_UNVERIFIED_UNIVERSE",
  "metrics": {
    "annual_return": 0.03851013509241685,
    "total_return": 0.35191575304731093,
    "sharpe": 0.5599042547253605,
    "max_drawdown": -0.11109295829434,
    "calmar": 0.34664784954582384
  },
  "promotion_allowed": false,
  "deployment_allowed": false
}
```

- [ ] **Step 2: Validate the pointer against the final manifest**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -c "import json; from pathlib import Path; c=json.loads(Path(r'experiments/fund_rotation_research_validity/champion.json').read_text(encoding='utf-8')); m=json.loads(Path(r'C:\Users\LK\.codex\visualizations\2026\08\29\01a04ca0-95f0-7cc1-b3eb-f1f868600e0e\real-chain-2015-2022-final\research_chain_manifest.json').read_text(encoding='utf-8')); r=next(x for x in m['stages'] if x['stage']=='batch_5')['comparison']['arms']['ai_rotation_r76_fixed_short_bond']; assert c['strategy_id']=='ai_rotation_r76_fixed_short_bond'; assert c['status']=='FROZEN_RESEARCH_CANDIDATE'; assert c['source']['batch_id']==m['batch_id']=='bc2c1d09c759'; assert c['source']['snapshot_fingerprint']==m['snapshot_fingerprint']; assert c['source']['evaluation_start_date']==m['evaluation_range']['evaluation_start_date']=='20150105'; assert c['source']['evaluation_end_date']==m['evaluation_range']['evaluation_end_date']=='20221230'; assert c['metrics']=={k:r[k] for k in ('annual_return','total_return','sharpe','max_drawdown','calmar')}; assert c['promotion_allowed'] is False and c['deployment_allowed'] is False; print('champion pointer validation passed')"
```

Expected output: `champion pointer validation passed`.

- [ ] **Step 3: Run the existing research regression tests**

Run:

```powershell
$env:PYTHONPATH='agent/src;agent'
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pytest -p no:cacheprovider --basetemp C:\Users\LK\.codex\visualizations\2026\08\29\01a04ca0-95f0-7cc1-b3eb-f1f868600e0e\pytest-r76-champion-pointer agent/tests/fund_rotation/test_research_continuation.py -q
```

Expected output: all collected tests pass.

- [ ] **Step 4: Request Luna review before Task 2**

Ask the existing 5.6 Luna reviewer to inspect only Task 1, verify the JSON values against the final manifest, confirm append-only history and research-only gates, and report P0/P1/P2/P3. Proceed only when P0=0 and P1=0.

- [ ] **Step 5: Commit Task 1**

```powershell
git add experiments/fund_rotation_research_validity/champion.json
git commit -m "feat: set R76 as research champion"
```

### Task 2: Change the frontend strategy-batch default

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx:23-24`
- Modify: `frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx:259-267`

**Interfaces:**
- Consumes: the backend strategy catalog already used by `FundRotationTab`.
- Produces: initial `variants` state with R76 when available, with R11 fallback unchanged.

- [ ] **Step 1: Change the frontend test expectation first**

Rename the test and change only its expected strategy ID:

```tsx
it("uses the requested default dates and r76 fixed short bond strategy", () => {
  render(<FundRotationTab />);

  expect(screen.getByLabelText("开始日期")).toHaveValue("2022-08-01");
  expect(screen.getByLabelText("结束日期")).toHaveValue("2026-08-01");
  expect(screen.getAllByRole("combobox")[0]).toHaveValue(
    "ai_rotation_r76_fixed_short_bond",
  );
});
```

- [ ] **Step 2: Run the focused frontend test and verify the expected failure**

From `frontend`, run:

```powershell
npm test -- --run src/components/stockpred/__tests__/FundRotationTab.test.tsx
```

Expected: the default strategy assertion fails because the implementation still selects R59.

- [ ] **Step 3: Make the minimal implementation change**

In `FundRotationTab.tsx`, change only:

```ts
const DEFAULT_STRATEGY_ID = "ai_rotation_r76_fixed_short_bond";
```

Leave this unchanged:

```ts
const FALLBACK_STRATEGY_ID = "ai_rotation_r11_persist_geom";
```

- [ ] **Step 4: Run the focused frontend test and verify it passes**

From `frontend`, run:

```powershell
npm test -- --run src/components/stockpred/__tests__/FundRotationTab.test.tsx
```

Expected output: all tests in the focused file pass.

- [ ] **Step 5: Run the frontend regression and build**

From `frontend`, run:

```powershell
npm test -- --run
npm run build
```

Expected: both commands exit with code 0.

- [ ] **Step 6: Request Luna review before Task 3**

Ask the existing 5.6 Luna reviewer to inspect only Task 2, verify the default/fallback behavior and diff scope, and report P0/P1/P2/P3. Proceed only when P0=0 and P1=0.

- [ ] **Step 7: Commit Task 2**

```powershell
git add frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx
git commit -m "feat: default fund rotation batches to R76"
```

### Task 3: Final cross-layer verification

**Files:**
- Verify: `experiments/fund_rotation_research_validity/champion.json`
- Verify: `frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx`
- Verify: `frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx`

**Interfaces:**
- Consumes: Task 1 Champion pointer and Task 2 frontend default.
- Produces: verified mapping from research Champion to frontend default without production promotion.

- [ ] **Step 1: Verify the two defaults are identical**

Run:

```powershell
$champion=(Get-Content -LiteralPath 'experiments/fund_rotation_research_validity/champion.json' -Raw | ConvertFrom-Json).strategy_id
$frontend=(Select-String -LiteralPath 'frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx' -Pattern 'const DEFAULT_STRATEGY_ID').Line
if ($frontend -notmatch [regex]::Escape($champion)) { throw "frontend default does not match champion: $champion" }
Write-Output "champion_and_frontend_default=$champion"
```

Expected output: `champion_and_frontend_default=ai_rotation_r76_fixed_short_bond`.

- [ ] **Step 2: Run final verification**

Run the Task 1 Python regression, the Task 2 frontend full test/build, `git diff --check`, and `git status --short --branch`.

Expected: all tests/build pass, `git diff --check` is clean, and no unrelated files are modified.

- [ ] **Step 3: Request final Luna review**

Ask the existing 5.6 Luna reviewer to inspect the complete diff and the final Champion pointer, with special attention to the distinction between research Champion and production promotion. Complete only with P0=0 and P1=0.

- [ ] **Step 4: Commit any remaining verification-only documentation change only if required**

Do not create extra code or documentation changes if the verification is clean; the two task commits and the already committed design/plan documents are sufficient.
