# 基金轮动聚类区间图 Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax.

Goal: 在基金轮动单次回测详情增加聚类区间 tab，以每次重聚类为边界，在一张图中展示归一化组合收益、基金收盘价和 B/S 标记。

Architecture: 复用既有候选池、收益和基金图表接口；新增纯函数/组件将日期区间切片并生成 ECharts series。详情面板只管理 tab 与按需加载，K 线 marker 继续使用现有 TradeMarkersChart 的状态/浅色语义。

Tech Stack: React 18、TypeScript、ECharts、Vitest、Testing Library、Tailwind CSS。

## Global Constraints

- 所有基金价格在每个重聚类区间首个有效收盘价处归一化为 1。
- 组合收益在每个重聚类区间首日归一化为 1。
- 重聚类日期为区间起点，下一重聚类日期前一天为区间终点。
- 复用现有 B/S 深浅色和拒绝/部分成交优先级，不修改后端协议。
- 避开工作区既有未提交的策略代码和测试改动。
- 遵循 TDD：先写失败测试，再写最小实现。

## 文件映射

- Create: frontend/src/components/stockpred/fund-rotation/ClusterIntervalChart.tsx
- Create: frontend/src/components/stockpred/fund-rotation/__tests__/ClusterIntervalChart.test.tsx
- Modify: frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx
- Modify: frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts
- Modify: frontend/src/components/stockpred/fund-rotation/types.ts
- Modify: frontend/src/components/stockpred/fund-rotation/deepLinks.ts only if tab union/URL validation requires it.
- Modify: frontend/src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx
- Modify: frontend/src/components/stockpred/fund-rotation/__tests__/useBacktestDetail.test.ts

## Task 1: 区间数据模型与纯函数

Files: Create ClusterIntervalChart.tsx; Test ClusterIntervalChart.test.tsx.

- [ ] 写失败测试：输入两次重聚类日期、两只基金 OHLCV、一条收益曲线，断言生成两个区间；每个区间首个有效价格和收益值为 1，后续点按首值归一化；下一重聚类日前的点不进入前一区间；无有效价格的基金不生成 series。
- [ ] 运行 npm --prefix frontend test -- --run src/components/stockpred/fund-rotation/__tests__/ClusterIntervalChart.test.tsx，确认因组件/适配器不存在而失败。
- [ ] 实现导出的纯函数 buildClusterIntervalChartModel(input)，输入为 ComparisonEquityData | null、CandidatePoolResponse | null、Record<string, InstrumentChartResponse>，输出 intervals、series、markPoints、boundaryLines；日期统一为连续数字日期字符串，使用区间内首个有效值归一化。
- [ ] 实现最小 ClusterIntervalChart，使用现有 ECharts 初始化模式；组合收益粗线、基金细线、重聚类边界线和对应基金 marker；空数据返回中文空态。
- [ ] 运行该测试文件确认通过。
- [ ] 提交 feat: add fund rotation cluster interval chart model。

## Task 2: 单次详情 tab 与按需加载

Files: Modify BacktestDetailPanel.tsx, useBacktestDetail.ts, types.ts; Test corresponding existing tests.

- [ ] 写失败测试：面板显示“聚类区间”按钮；active tab 为该 tab 时触发 loadCandidatePool 和 loadCharts，已有缓存不重复加载；将候选池和图表传入 ClusterIntervalChart。
- [ ] 运行 BacktestDetailPanel 测试确认失败。
- [ ] 扩展 BacktestDetailTab 为 cluster_interval；在 hook 增加该 tab 的 URL 恢复/同步兼容，复用现有候选池与图表缓存状态，不新增重复请求状态。
- [ ] 在面板加入 tab 和内容分支，加载失败/无数据由图表组件显示中文状态，已有 K 线和候选池 tab 保持行为。
- [ ] 运行 BacktestDetailPanel.test.tsx 与 useBacktestDetail.test.ts 确认通过。
- [ ] 提交 feat: add cluster interval tab to fund rotation detail。

## Task 3: 视觉与回归验证

Files: 相关前端文件，无新增业务范围。

- [ ] 检查图表 marker 颜色分支与 CandlestickChart 当前 muted/status 分支一致，不复制出新的交易状态规则。
- [ ] 运行相关 Vitest：BacktestDetailPanel.test.tsx、ClusterIntervalChart.test.tsx、useBacktestDetail.test.ts。
- [ ] 运行 npm --prefix frontend run build。
- [ ] 运行 git diff --check，确认差异不包含既有策略改动。
- [ ] 子 agent 进行最终 whole-branch review；若有 Critical/Important finding，交由子 agent 修复并复审。
