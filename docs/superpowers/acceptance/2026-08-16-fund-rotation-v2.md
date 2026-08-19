# 基金轮动前端 V2 验收记录

日期：2026-08-16

## 已完成范围

- 新增 `rotation_analysis` 标签页，保持概览、收益曲线、K 线证据和候选池标签稳定。
- 后端发布实际持仓时间线、调仓索引、调仓决策证据和策略指标证据产物。
- 新增持仓连续区间图、Cash 行、调仓边界、时间窗口刷选和语义缩放。
- 新增 Before/After、Cluster Quality、Representative、Ranking、执行摘要和候选池筛选。
- K 线证据接入运行时策略指标，默认展示后端 Momentum；无产物时明确降级。
- 接入 `run_id / detail_tab / signal_date / instrument / focus_date / strategy_indicator` URL 状态，支持刷新、浏览器回退和跨标签跳转。
- 时间轴日期计算使用真实 UTC 日期戳，支持跨月、跨年和多年窗口。

## 验证结果

- 前端基金轮动测试：18 个测试文件，77 个测试通过。
- 前端生产构建：通过。
- 后端 V2 变更涉及回归：80 个通过，3 个跳过，1 个 FastAPI/HTTPX 弃用警告。
- 后端 V2 证据/API专项测试：9 个通过，1 个弃用警告。
- `git diff --check`：通过。

## 全量基线差异

完整前端测试还存在 1 个不属于本次 V2 改动的既有失败：

`GraphSignalPanel` 测试仍期望 y 轴名称为 `Score`，现有组件实际使用 `stockPred.score`。

完整后端基金轮动目录还存在 1 个不属于本次 V2 改动的既有失败：

`test_chart_reads_nonempty_ohlcv_from_v2_pinned_fund_version` 的 Lance mock 只期望读取 `fund.lance`，现有接口同时读取快照声明的 `dim_fund.lance`；该失败发生在本次新增策略证据读取之前，未修改其既有维度读取逻辑。

## 说明

当前工作区的 `.git` 索引文件受到环境权限限制，无法创建 `.git/index.lock`，因此未执行提交；代码和中文设计/计划/验收文档均已写入工作区。
