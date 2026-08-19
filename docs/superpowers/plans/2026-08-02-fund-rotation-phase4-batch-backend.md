# Phase 4：策略批次后端、API 与持久化实施计划

> **执行要求：** 使用 `executing-plans` 技能实施。批次内部在本阶段固定顺序执行，禁止提前引入子运行并发。

**目标：** 提供统一策略批次 API，在同一快照和评价上下文中运行一个或多个策略变体，并用追加式 JSON/CSV 持久化状态、事件和公平比较结果。

**架构：** 服务接收规范化批次请求，校验幂等性与 Catalog 配置，固定一次公共快照，顺序调用 Runner；父任务使用有界 executor。批次和子运行拥有版本化状态机，SSE 使用批次级单调序号。

**技术栈：** FastAPI、Pydantic、ThreadPoolExecutor、JSON/CSV、pytest。

---

## Task 1：定义 API 模型和目录读取端点

**文件：**

- 新建：`agent/src/stockpred/fund_rotation/api_models.py`
- 修改：`agent/src/api/fund_rotation_routes.py`
- 新建：`agent/tests/fund_rotation/test_fund_rotation_catalog_api.py`

**端点：**

- `GET /stockpred/fund-rotation/strategies`
- `GET /stockpred/fund-rotation/strategies/{strategy_id}`

**步骤：**

1. 写 API 测试：列表稳定排序，并返回 Catalog 版本、策略 ID/名称/说明、接口版本、实现哈希、默认配置解析出的预热长度/数据集/字段和支持的 ETF 池类型；详情额外包含 schema、解析后 defaults、参数说明、RESEARCH_ONLY 警告和策略专用产物角色。
2. 未知策略返回结构化 `FUND_ROTATION_STRATEGY_NOT_FOUND`；损坏 Catalog 或 `FUND_ROTATION_INTERFACE_INCOMPATIBLE` 应在后台任务创建前失败，不返回半份列表。
3. 响应直接来自 Catalog，不在路由层复制策略字段。
4. 给 schema 响应添加版本和 ETag/内容哈希，便于前端缓存。

**提交：** `feat(fund-rotation): expose strategy catalog API`

## Task 2：实现批次请求规范化和幂等性

**文件：**

- 新建：`agent/src/stockpred/fund_rotation/batch_models.py`
- 新建：`agent/src/stockpred/fund_rotation/batch_persistence.py`
- 新建：`agent/tests/fund_rotation/test_batch_idempotency.py`

**请求核心：** 必填 `schema_version`、`idempotency_key`、`mode="RESEARCH_ONLY"`，以及评价区间、公共执行配置和 `variants[{strategy_id, label, params}]`。服务使用 `strategy_id + "@" + resolved_config_hash[:12]` 生成稳定 `variant_key`；展示标签不进入身份，同一策略和同一解析配置不得重复。

**步骤：**

1. 写测试：同一幂等键 + 同一规范化客户端载荷返回原批次；同键不同载荷返回 `409 IDEMPOTENCY_CONFLICT`；键或 `schema_version` 为空返回 422；mode 缺失、大小写变体或任何非 `RESEARCH_ONLY` 值都在创建后台任务前拒绝。
2. 幂等哈希使用 `schema_version` 加规范化客户端载荷，只做 JSON 类型、对象 key 排序和已声明别名处理，不重新解析策略默认值，也不把服务端生成 batch_id、时间戳和快照混入。
3. 作用域按当前单用户本地服务全局处理；若未来引入用户身份，再把用户 ID 加入复合键。
4. 用原子临时文件替换写 index/manifest；事件日志只追加，不使用数据库。
5. 另存 `resolved_batch_identity`，包含每个 Variant 的 `variant_key`、解析配置/需求/实现哈希及公共框架身份；它与客户端幂等哈希不得混为一个哈希。
6. 服务重启后同键不得“继续”失败批次；同载荷仍返回原批次，重新计算需新键。

**提交：** `feat(fund-rotation): persist batch idempotency without database`

## Task 3：实现版本化批次/子运行状态和事件

**文件：**

- 修改：`agent/src/stockpred/fund_rotation/state_machine.py`
- 修改：`agent/src/stockpred/fund_rotation/persistence.py`
- 新建：`agent/tests/fund_rotation/test_batch_state_machine.py`
- 修改：`agent/tests/fund_rotation/test_persistence.py`

**步骤：**

1. 定义 batch v2 状态 `QUEUED → VALIDATING → SNAPSHOTTING_DATA → RUNNING_STRATEGIES → COMPARING → WRITING_RESULTS → SUCCEEDED | PARTIAL_SUCCEEDED | FAILED | CANCELED | FAILED_INTERRUPTED`；child v2 使用 `QUEUED → PREPARING_DATA → GENERATING_SIGNALS → EXECUTING → COMPUTING_METRICS → WRITING_RESULTS → SUCCEEDED | FAILED | CANCELED | FAILED_INTERRUPTED`。
2. 定义统一事件 envelope：`{schema_version, seq, ts, event_type, scope, batch_id, run_id, variant_key, strategy_id, stage, strategy_substage, progress:{completed,total,unit,ratio}, message, error}`。时间字段严格使用设计 §30.1 的 `ts`；`event_type` 至少支持 `BATCH_STAGE/VARIANT_STAGE/VARIANT_PROGRESS/TERMINAL/ERROR`，scope 为 BATCH 或 VARIANT；未知 substage 不能驱动状态机。
3. 批次事件先原子追加/持久化，再发布 SSE，并分配严格递增全局 `seq`；子运行内部 seq 仅作诊断。校验 completed/total 为非负整数、completed≤total、同 unit 进度不倒退、ratio 由二者计算。
4. 保留 v1 状态名解析映射，只读展示 `PREPARING_RETURNS/CLUSTERING/GENERATING_TARGETS`，不重写历史文件。
5. 启动扫描全部 v1/v2 非终态批次并标记 `FAILED_INTERRUPTED`，保留已完成子运行产物；用户主动取消使用 `CANCELED`，不得与进程中断混淆，也不自动恢复。

**提交：** `feat(fund-rotation): add versioned batch state and event log`

## Task 4：实现顺序批次编排和失败隔离

**文件：**

- 新建：`agent/src/stockpred/fund_rotation/batch_service.py`
- 修改：`agent/src/stockpred/fund_rotation/service.py`
- 新建：`agent/tests/fund_rotation/test_batch_service.py`

**步骤：**

1. 先固定完整交易日历和 Lance 版本标识但不扫描行情；各 Variant 从日历最早日向后满足自身 warmup 得到 `provisional_start`，创建只允许排程、尚不调用 evaluate 的 Session，以 `scheduled_dates(calendar, provisional_start, evaluation_end_date)` 找到评价首日前最近决策日，再从该日向前回溯自身 `warmup_trade_days`，得到真实数据起点和 `simulation_start_date`。不存在预评价决策日时在启动前报告历史不足。父批次取所有 Variant 数据起点的最早值，从已固定版本只读取一次共享数据并形成 `PinnedFundDataSnapshot`/`EvaluationContext`。写测试证明这不是简单的 `evaluation_start - max(warmup)`，排程不读取行情，且所有 Variant 引用相同指纹。
2. 父批次进入有界 `ThreadPoolExecutor(max_workers=1)`；不得为每个请求创建无限 daemon thread。
3. 先生成并校验全部 `variant_key`，再严格按 `variant_key` 稳定排序运行；请求顺序变化不得改变执行顺序或规范化结果。每个 Variant 创建独立 strategy/session/data view/result 目录。
4. 一个子运行 INVALID/异常仅标记该子运行失败，之后 variant 继续；公共快照失败则整个批次失败。
5. 安全取消只设置 token，在决策日、执行日和产物发布边界检查；尚未开始的 Variant 不启动，当前子运行转为 CANCELED，已经 SUCCEEDED 的子运行保持只读，父批次最终为 CANCELED 且不发布完整比较 Manifest，不声称支持断点续跑。
6. 父状态使用 `SUCCEEDED/PARTIAL_SUCCEEDED/FAILED/CANCELED/FAILED_INTERRUPTED`；全部成功为 SUCCEEDED，成功与失败混合为 PARTIAL_SUCCEEDED，父级准备失败或全部子运行失败为 FAILED，主动取消为 CANCELED，服务异常中断为 FAILED_INTERRUPTED。

**提交：** `feat(fund-rotation): orchestrate bounded sequential strategy batches`

## Task 5：生成严格可比结果

**文件：**

- 新建：`agent/src/stockpred/fund_rotation/comparison.py`
- 新建：`agent/tests/fund_rotation/test_batch_comparison.py`

**步骤：**

1. 写测试：只有成功且 equity index 与公共 `evaluation_calendar` 完全相等的子运行参与比较。
2. 决策动作 INVALID、技术失败、缺日或多日策略列入 excluded 列表并记录稳定原因；不得通过日期交集缩短区间。`quality_status=INVALID` 但技术成功的子运行仍保留完整净值和诊断。
3. 从各策略原始 equity 和共同 `initial_nav=1.0` 重新计算比较指标，不复用可能口径不同的展示字段。
4. 比较指纹明确由八个分量生成：`framework_implementation_hash`、`data_snapshot_fingerprint`、`evaluation_calendar_hash`、`universe_policy_version`、`return_policy_version`、`execution_contract`、`benchmark_contract_version`、`metric_contract_version`；不包括策略实现/配置，另存每个 variant identity hash。
5. 实现研究质量四态 `VALID/DEGRADED/INVALID/FAILED`；默认排名仅纳入 VALID/DEGRADED。INVALID 显示净值与无效警告但不排名，FAILED 不以零收益进入比较。
6. 父批次目录严格写入 `request.json/resolved_batch.json/state.json/events.jsonl/data_snapshot.json/reports.json/comparison_equity.csv/comparison_metrics.csv/manifest.json`；使用 `manifest.json` 作为唯一、最后的原子发布点。父批次只引用子运行 ID，不复制子产物。

**提交：** `feat(fund-rotation): build strict common-calendar comparisons`

## Task 6：接入统一批次和读取 API

**文件：**

- 修改：`agent/src/api/fund_rotation_routes.py`
- 新建：`agent/tests/fund_rotation/test_fund_rotation_batch_api.py`

**端点：**

- `POST /stockpred/fund-rotation/strategy-batches`
- `GET /stockpred/fund-rotation/strategy-batches`
- `GET /stockpred/fund-rotation/strategy-batches/{batch_id}`
- `POST /stockpred/fund-rotation/strategy-batches/{batch_id}/cancel`
- `GET /stockpred/fund-rotation/strategy-batches/{batch_id}/events`
- `GET /stockpred/fund-rotation/strategy-batches/{batch_id}/artifacts/{artifact_id}`
- `GET /stockpred/fund-rotation/backtests/{run_id}`
- `GET /stockpred/fund-rotation/backtests/{run_id}/artifacts/{artifact_id}`
- `GET /stockpred/fund-rotation/backtests/{run_id}/instruments/{ts_code}/chart`

**步骤：**

1. API 测试覆盖单 variant 和多 variant 使用同一 POST 结构，并验证请求 mode 只能是 RESEARCH_ONLY。
2. SSE 支持 `Last-Event-ID` 对应父批次全局 `seq`，重连只补发之后事件且不漏其他 Variant；完成后发送终态并关闭。
3. artifact_id 必须来自 manifest 白名单，路径穿越和任意文件读取返回 404；K 线端点从标准成交、订单和价格产物返回 signal/order/fill/blocked 标记所需字段。
4. 批次、子运行、所有 API 响应和 Manifest 均持久化 `mode=RESEARCH_ONLY`；不存在下单/实盘转发端点，也不能注册为实时自动化信号源。
5. 此阶段保留旧 POST 创建回测路由供 Phase 5/6 迁移前内部兼容，同时保留上述 `backtests` GET 作为长期子运行只读接口。

**提交：** `feat(fund-rotation): expose unified strategy batch API`

## Task 7：性能、重启和整体验收

**文件：**

- 新建：`agent/tests/fund_rotation/test_batch_recovery.py`
- 新建：`agent/tests/fund_rotation/test_batch_performance.py`

**步骤：**

1. 模拟进程中断后重建 service，断言状态转为 FAILED_INTERRUPTED、已完成子运行仍可读、不会自动重新运行。
2. 用 1/3/10 个轻量假策略测批次内最大并行度始终为 1，父任务并发不超过配置。
3. 记录 5 年日频单策略和 10 策略的耗时/峰值内存基线；本阶段只建立可观测基线，不以未确认数字作硬优化目标。
4. 运行全套后端测试并人工检查一个批次目录的 JSON/CSV 可读性。

**验证：**

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation\test_fund_rotation_batch_api.py agent\tests\fund_rotation\test_batch_recovery.py -q
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation -q
git diff --check
```

**提交：** `test(fund-rotation): verify batch backend lifecycle`

## Phase 4 出口门禁

- Catalog、单策略批次和多策略批次 API 可用。
- 同一批次共享快照/日历，子运行顺序且隔离。
- 幂等、取消、重启判定、SSE 续读和严格比较均有自动化测试。
- 持久化只使用 JSON/CSV，且无断点续跑承诺。
