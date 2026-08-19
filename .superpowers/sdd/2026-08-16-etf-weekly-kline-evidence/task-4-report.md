# Task 4 验证报告：回归验证与页面级接线

## 状态

验证完成。工作区：`E:\code\stock\Vibe-Trading\.worktrees\weekly-etf-kline-evidence`。

## 验证命令

1. `npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation src/pages/__tests__/StockPred.test.tsx`
   - 首次在受限沙箱中退出码 `1`：Vitest 配置加载阶段 `esbuild` 子进程 `spawn EPERM`，未执行测试用例。
   - 使用同一命令在允许子进程启动的环境重跑，退出码 `0`。
   - `Test Files 11 passed (11)`；`Tests 62 passed (62)`；失败 `0`。
2. `npm --prefix frontend run build`
   - 退出码 `0`。
   - `tsc -b && vite build` 成功；`2726 modules transformed`，生产包构建完成。
3. `git diff --check`
   - 退出码 `0`；无 whitespace 错误。
4. `git status --short`
   - 退出码 `0`；无工作区改动。
5. 计划文件核对：`git ls-files -- ...`
   - 退出码 `0`；Task 2/3 报告及计划中的生产、测试文件均存在。
6. 页面接线核对：对 `BacktestDetailPanel.tsx`、`WeeklyKlineEvidence.tsx`、`StockPred.tsx` 及 `StockPred.test.tsx` 搜索旧 ETF selector 相关文本。
   - 退出码 `0`；未发现旧 ETF selector；周线组件改为按 evidence instrument 渲染。

## 范围观察

- `BacktestDetailPanel.test.tsx` 已覆盖 detail 无 instrument 时不调用 `loadCharts()` 的空状态 guard，也覆盖有 instrument 时只批量加载一次。
- `BacktestDetailPanel.tsx` 的图表页已移除旧单 ETF 下拉选择，接线到周度 ETF K 线证据组件。
- `git diff --stat` 为空、`git status --short` 为空；本次验证未产生代码修改，因此没有提交。
- 未发现与本任务无关的测试失败。

## concerns

- 构建仍输出项目既有的 chunk size warning：部分压缩后 chunk 大于 500 kB；本次未修改相关构建配置。
- 受限沙箱中的首次测试启动 `spawn EPERM` 已通过允许子进程启动环境的同命令复验，不属于代码回归。

## Final review fix wave（2026-08-16）

### 修复内容

1. `WeeklyKlineEvidence.tsx` 对同一周内的所有 ETF 图表统一传入周范围 `weekStart`—`weekEnd`，不再使用 instrument 私有 `coreDates`。
2. `TradeMarkersChart.tsx` 在提供 `dateRange` 时统一过滤信号、交易、K 线和图中 marker；摘要计数及下方信号、成交/阻断详情均使用过滤后的数组；未提供范围时保持原行为。
3. `BacktestDetailPanel.test.tsx` 移除对 `WeeklyKlineEvidence` 的整体 mock，使用两周、两只 ETF 的真实面板接线验证周标题顺序、两只 ETF 图表与右侧证据内容，并确认不存在旧 ETF selector。

### TDD 验证

命令：

```text
npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx
```

输出摘要：

```text
Test Files  3 passed (3)
Tests       9 passed (9)
```

测试先于生产代码执行并按预期暴露周范围和交易/信号过滤问题；面板测试首次运行仅因 jsdom 无 canvas 上下文中断，随后改用无 OHLCV 的 chart response 保留面板接线覆盖。

### 最终验证命令与输出

命令：

```text
npm --prefix frontend run test:run -- src/components/stockpred/fund-rotation src/pages/__tests__/StockPred.test.tsx
```

输出：

```text
Test Files  11 passed (11)
Tests       64 passed (64)
```

命令：

```text
npm --prefix frontend run build
```

输出摘要：

```text
vite v6.4.3 building for production...
✓ 2726 modules transformed.
✓ built in 4.83s
```

命令：

```text
git diff --check
```

输出：无 whitespace 错误；Git 仅提示工作副本的 LF/CRLF 转换警告。

### concerns

- 构建保留项目既有的 chunk size warning（`vendor-charts` 压缩后超过 500 kB）；本修复未改动构建分包配置。
- 首次受限沙箱测试命令输出 `Error: spawn EPERM`，同一命令在允许 esbuild 子进程的环境中通过；不属于代码测试失败。
