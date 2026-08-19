# 基金轮动单次回测详情与候选池展示设计

## 目标

在“单次回测详情”页面补充当前详情接口缺失的任务总览和执行生命周期信息，并新增“基金候选池”标签页，展示每次重聚类的簇摘要和最终代表基金信息。保留现有概览、收益曲线和 K 线证据内容，不重复显示已有字段。

## 范围与约束

- 仅覆盖已发布且通过 manifest checksum 校验的 v2 基金轮动运行。
- 概览页只增加当前页面尚未展示的任务总览和执行生命周期字段；已有运行范围、可复现身份、策略参数和核心指标保持原样。
- 候选池采用代表基金视图：每次重聚类展示 8 个簇的摘要和代表选择结果，不展开全部簇成员。
- 基金名称和分类必须读取本次回测 `data_snapshot.json` 锁定的 `dim_fund` 版本，不能读取最新未锁定版本。
- 当前维表没有 `asset_class` 或 `instrument_type` 时，分类使用 `fund_type`；字段缺失时返回空值，由前端显示“—”。
- 候选池接口失败不得影响概览、收益曲线和 K 线证据标签页。

## 后端设计

新增接口：

```text
GET /stockpred/fund-rotation/backtests/{run_id}/candidate-pool
```

接口先调用现有 `_published_manifest` 和 `_validated_artifact`，只读取 manifest 声明且 checksum 校验通过的以下文件：

- `strategy_cluster_history.json`
- `strategy_gates.json`
- `strategy_representatives.json`
- `data_snapshot.json`

响应包含按重聚类日期排序的列表。每次重聚类包含日期、参与聚类基金数量、簇规模、门禁结果和 8 个簇行。每个簇行包含：

- `cluster_id`
- `cluster_size`
- `selected_code`
- `selected_name`
- `selected_fund_type`
- `lock_maintained`
- `exclusion_reason`

门禁字段包含 `overall`、最大簇占比、有效簇数量及对应门禁结果。代表元数据通过 snapshot 中的 `dim_version` 打开 `dim_fund.lance`，一次性读取所有代表基金代码，避免前端为每行发起独立请求。

对于没有代表基金、维表没有对应代码或字段缺失的情况，接口仍返回结构化行，代码为空或保留，名称/分类为空，不因单行元数据缺失而失败。

## 前端设计

扩展 `BacktestDetailTab`，新增 `candidate_pool` 标签。

概览页增加两块：

1. 任务总览：仅展示当前页面没有的 Run ID、批次 ID、变体、策略、运行模式、状态、质量状态、部分完成、结果发布和比较资格等信息；不复制现有运行范围和身份区域中的字段。
2. 执行生命周期：始终展示 `detail.events`，按事件序号排序，显示时间、阶段、消息和错误。成功运行也必须显示完整事件，不再受 `!detail.result_published` 条件限制。

候选池标签页进入时懒加载接口数据，按重聚类日期渲染卡片。每张卡片显示聚类日期、参与基金数量、最大簇占比、有效簇数量、门禁总体状态，并用表格显示 8 个簇的代表代码、名称、分类、簇规模、是否延续和选择/排除原因。

## 数据流

```text
BacktestDetailPanel
  └─ useBacktestDetail.selectTab("candidate_pool")
       └─ fetchCandidatePool(runId)
            └─ GET /backtests/{run_id}/candidate-pool
                 ├─ checksum-gated strategy artifacts
                 └─ pinned dim_fund metadata
```

候选池加载状态和错误状态独立于详情主加载状态；切换运行或关闭详情时清空候选池数据，使用请求序号和 AbortController 防止旧运行响应覆盖当前页面。

## 测试设计

- 后端：发布包含四个策略工件和 snapshot 的测试运行，验证候选池响应按日期和簇排序，包含代表名称与 `fund_type`；验证未发布运行返回错误；验证缺少元数据时仍返回代码和空名称/分类。
- 前端 hook：验证切换到候选池标签只加载当前运行，切换运行时旧请求不会覆盖新数据，加载错误不清空其他详情状态。
- 前端组件：验证概览已有内容不重复，任务总览和完整生命周期可见；验证候选池标签、8 个簇行、代表代码/名称/分类、门禁结果可见；验证无数据和加载错误状态。
- 回归：运行基金轮动详情相关前端测试、后端基金轮动 API 测试和 TypeScript 构建。
