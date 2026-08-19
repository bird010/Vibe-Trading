# Task 2 报告：多 ETF 并发加载

## 状态

已完成。实现了多 ETF K 线证据并发加载、逐 ETF 错误保留，以及切换/关闭 run 时的过期请求保护。旧的 `chart`、`selectedInstrument`、`selectInstrument` 行为保持不变。

## 变更文件

- `frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts`
  - 在 `BacktestDetailState` 增加 `charts`、`chartLoading`、`chartErrors` 和 `loadCharts`。
  - `loadCharts` 使用 `Promise.allSettled` 并发请求当前 detail 中所有 instrument，成功结果按 ETF 代码写入 map，非 abort 拒绝写入可读错误。
  - 增加独立的 charts abort controller 与 generation counter。
  - `openRun`、`closeRun` 清空 chart map/error，并使旧请求失效。
- `frontend/src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts`
  - 覆盖双 ETF 成功加载、单 ETF 失败但保留另一响应、旧 run 延迟响应不能写入当前 run。

`types.ts` 已包含本任务所需的 `InstrumentChartResponse`，无需改动。

## 测试与构建

1. TDD 红灯前置运行：

   `npm test -- --run src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts`

   首次在沙箱内因 Vitest/esbuild 启动时报 `Error: spawn EPERM`，未进入断言；使用所需进程权限复跑后，测试结果为 `Test Files 1 passed (1)`、`Tests 7 passed (7)`。

2. 聚焦测试（实现后）：

   `Test Files 1 passed (1)`、`Tests 7 passed (7)`。

3. 构建：

   `npm run build`

   TypeScript 检查和 Vite 构建均成功，输出 `✓ built in 4.77s`。Vite 仅报告既有的大 chunk warning。

4. 完整测试：

   `npm run test:run`

   `39 passed` test files、`311 passed` tests；另有 2 个与本任务无关的既有失败：

   - `src/components/charts/__tests__/GraphSignalPanel.test.tsx`：测试期待 yAxis 名称为 `Score`，实际为 `stockPred.score`。
   - `src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`：测试用单一文本节点匹配摘要，实际渲染被拆成多个节点。

## 提交

提交信息：`feat: load fund rotation charts by instrument`

提交 hash：`e84bffb78c206c856e7f07b4633af2590d3faf9f`

## 关注事项

- 完整测试仍受上述两个无关既有失败影响；本任务聚焦测试和构建均通过。
- 本任务未修改 `types.ts`，因为 `InstrumentChartResponse` 已存在且满足新增状态字段需求。
