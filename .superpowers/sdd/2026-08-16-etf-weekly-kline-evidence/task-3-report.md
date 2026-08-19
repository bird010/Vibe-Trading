# Task 3 实现报告：周度 ETF K 线证据布局

## 状态

已完成。实现工作区：`E:\code\stock\Vibe-Trading\.worktrees\weekly-etf-kline-evidence`。

## 变更文件

- `frontend/src/components/stockpred/fund-rotation/WeeklyKlineEvidence.tsx`
  - 新增按周升序渲染的响应式双栏证据布局。
  - 左侧按 ETF 渲染紧凑 K 线，右侧展示中文证据表：目标权重、交易状态、成交数量、成交价格、阻断原因。
  - 支持加载、空数据、部分失败、全失败可重试状态；可用 ETF 图表不会因其他 ETF 失败而隐藏。
- `frontend/src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx`
  - 覆盖两周时间顺序、双栏内容、部分 ETF 失败仍保留成功图表、加载/空数据/全失败重试。
- `frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx`
  - 新增可选 `dateRange` 与 `height`。
  - 日期范围存在时，对 K 线和交易标记做包含边界的日期归一化过滤；无范围时保持原行为。
- `frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`
  - 新增日期范围包含边界过滤测试，并修正既有跨文本节点断言。
- `frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx`
  - 图表页改为一次批量 `loadCharts()`，使用 `groupWeeklyEvidence(Object.values(charts))` 渲染新组件。
  - 移除单 ETF 下拉选择和旧单图分支，保留无 instrument 空状态及图表来源/错误语义。
  - 不再在迁移后的图表页并发触发旧的单图加载流程。

## 提交

- 实现提交：`49cb9d2af2cbbb8cbc788d17d8f683aa8b329691`（`feat: render weekly ETF kline evidence`）
- 报告文件按任务要求单独交付；其路径受仓库 `.gitignore` 忽略，未纳入实现提交。

## 验证命令与输出

1. `npm run test:run -- src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`
   - `Test Files 2 passed (2)`
   - `Tests 5 passed (5)`
2. `npm run test:run -- src/components/stockpred/fund-rotation/__tests__`
   - `Test Files 9 passed (9)`
   - `Tests 47 passed (47)`
3. `npm run build`
   - `tsc -b && vite build` 成功，退出码 `0`。
4. `git diff --check`
   - 通过；仅报告工作区换行符将由 Git 从 LF 转为 CRLF 的提示。

## TDD 记录

- RED：新组件测试首先因 `WeeklyKlineEvidence` 尚不存在而失败；日期范围测试首先确认现有组件未过滤日期范围。
- GREEN：补充最小实现后，焦点测试通过。
- 最终回归：基金轮动测试 47/47 通过，构建通过。

## concerns

- Vite 构建保留项目已有的 chunk size warning：部分压缩后 chunk 大于 500 kB；本任务未引入依赖，也未扩大处理范围。

## Round 1 review fix

### 修复内容

- `BacktestDetailPanel` 的图表页 effect 现在要求 `detail.instruments.length > 0` 才会调用批量加载，保留无 instrument 的详情级空状态，并避免空数组触发状态对象更新循环。
- `useBacktestDetail.loadCharts()` 增加同样的空 instrument 早退保护，避免其他调用路径对空数组执行空批量请求。
- 新增 `BacktestDetailPanel.test.tsx` 生命周期回归测试：无 instrument 时断言不调用 `loadCharts()`；有 instrument 时断言批量加载只触发一次。
- 按评审意见保留周度组件只渲染有周度证据的 instrument，未扩展到全 universe。

### Round 1 TDD 与验证

- RED：`npm run test:run -- src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx`：`1 failed, 1 passed`；失败为无 instrument 时 `loadCharts` 实际被调用 1 次。
- GREEN：`npm run test:run -- src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`：`Test Files 3 passed (3)`，`Tests 7 passed (7)`。
- `npm run build`：成功，`tsc -b && vite build` 退出码 `0`；保留既有 chunk size warning。

### Round 1 提交

- 修复提交：`8ab5dbd3987a78eec169136b1bbb1bb665d0f58a`（`fix: guard empty ETF chart loads`）。
