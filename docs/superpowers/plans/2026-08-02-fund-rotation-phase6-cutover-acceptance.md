# Phase 6：调用方切换、旧接口清理与总验收实施计划

> **执行要求：** 使用 `executing-plans` 和 `verification-before-completion` 技能实施。本阶段只有在 Phase 0–5 全部门禁通过后才能开始。

**目标：** 将所有基金轮转创建调用方切换到统一策略批次 API，删除旧写接口和旧内部接口，同时保留历史运行只读能力，并完成真实数据、性能和回滚验收。

**架构：** 不提供双模式开关。新 UI 和所有程序调用方统一使用 strategy-batches；旧 v1 运行通过兼容 reader 读取，不能创建新的 v1 运行。

**技术栈：** Python、FastAPI、React、TypeScript、pytest、Vitest。

---

## Task 1：枚举并迁移所有调用方

**文件：**

- 修改：`frontend/src/components/stockpred/fund-rotation/useFundRotation.ts`
- 按搜索结果修改：所有调用 `/stockpred/fund-rotation/backtests` 或旧 defaults 的脚本、测试和文档示例
- 新建：`agent/tests/fund_rotation/test_no_legacy_create_callers.py`

**步骤：**

1. 执行搜索并保存结果：

```powershell
rg -n "fund-rotation/(backtests|defaults)|FundRotationConfig|run_fund_rotation_backtest" agent frontend scripts
```

2. 逐项分类为生产调用方、测试、历史 reader 或文档；只迁移创建行为，历史读取暂保留。
3. 每个调用方改为统一 batch request，即使只有一个 variant。
4. 写架构测试阻止新增旧 URL 或旧配置类型引用。
5. 在删除旧接口前运行所有受影响调用方测试。

**提交：** `refactor(fund-rotation): migrate callers to strategy batches`

## Task 2：实现并验证历史 v1 只读兼容

**文件：**

- 新建：`agent/src/stockpred/fund_rotation/legacy_reader.py`
- 修改：`agent/src/stockpred/fund_rotation/persistence.py`
- 修改：`agent/src/api/fund_rotation_routes.py`
- 新建：`agent/tests/fund_rotation/test_legacy_run_reader.py`

**步骤：**

1. 复制最小匿名化 v1 fixture，包含旧状态名、事件和常用产物。
2. reader 根据 schema/version 分派；无版本的历史文件按 v1 处理，但损坏文件返回明确错误。
3. 保持旧 GET 列表、详情、events、artifacts 和 chart 所需数据可读；响应可转换成统一只读 view model，但不得回写原目录。
4. 旧运行显示 legacy/research_only 标识，缺少新字段时返回 N/A，不虚构比较指纹。
5. 测试真实目录 `agent/runs/fund_rotation/bac86bdddcf85601`（存在时）只读打开，测试不得改写其中任何文件。

**提交：** `feat(fund-rotation): preserve read-only legacy runs`

## Task 3：删除旧创建接口和旧默认配置接口

**文件：**

- 修改：`agent/src/api/fund_rotation_routes.py`
- 修改：`agent/src/stockpred/fund_rotation/service.py`
- 修改：`agent/api_server.py`（仅当路由注册方式需要调整）
- 修改：相关 API 测试

**步骤：**

1. 先写测试：旧 POST `/stockpred/fund-rotation/backtests` 和旧 defaults 返回 404/410（按项目约定选定一个并固定），新批次 POST 正常。
2. 删除旧创建 handler、请求模型和 service 入口；保留历史 GET reader。
3. 确认不存在基于环境变量或 UI toggle 的旧模式后门。
4. 运行 API 路由注册测试，确保没有重复前缀或 OpenAPI 冲突。

**提交：** `refactor(fund-rotation): remove legacy write endpoints`

## Task 4：删除旧内部接口和孤儿代码

**文件：**

- 修改/删除：`agent/backtest/fund_rotation/config.py`
- 修改/删除：`agent/backtest/fund_rotation/pipeline.py`
- 修改：`agent/backtest/fund_rotation/__init__.py`
- 修改：对应 tests

**步骤：**

1. 用 `rg` 证明旧 dataclass、兼容 adapter 和旧 pipeline 公开函数没有生产调用方。
2. 删除 Phase 1/2 为迁移临时建立的单向 adapter。
3. 若 `pipeline.py` 已无调用方则删除；若仍作为稳定薄入口被新代码使用，重命名为明确的 runner facade，并在同一提交迁移引用。
4. 只删除因本次迁移形成的孤儿；无关历史死代码另列问题，不顺手清理。
5. 架构测试确保策略算法只位于 strategies 包，执行逻辑只有公共实现。

**提交：** `refactor(fund-rotation): remove legacy internal pipeline`

## Task 5：端到端真实数据验收

**文件：**

- 新建：`agent/tests/fund_rotation/test_local_research_smoke.py`（默认标记为 integration）
- 新建：`docs/superpowers/plans/2026-08-02-fund-rotation-acceptance-record.md`

**步骤：**

1. 使用本地 Lance 中名称包含 ETF 且不包含 QDII/LOF 的固定版本构造池；缺 fund_adj 历史的 ETF 按设计排除，不插值、不回退裸收盘价。
2. 运行包含 baseline 和 correlation representative 的同一批次，保存 batch_id、版本、比较指纹和运行环境。
3. 人工抽查至少两个调仓日：信号数据截止日、下一交易日订单、100 份取整、费用、ADV20 容量、成交后持仓和日终净值。
4. 人工抽查一个代表 ETF：medoid 邻域、候选与 leave-one-out cluster index 的相关性阈值、ADV20 排名、锁定/回退原因。
5. 在 UI 核对净值比较、指标、子运行诊断和 K 线买卖标记。
6. 所有结果标记 RESEARCH_ONLY；收益高低不作为功能验收条件。

**提交：** `test(fund-rotation): record real-data acceptance evidence`

## Task 6：性能和资源验收

**文件：**

- 修改：`agent/tests/fund_rotation/test_batch_performance.py`
- 修改：`docs/superpowers/plans/2026-08-02-fund-rotation-acceptance-record.md`

**步骤：**

1. 在同一数据快照测 1、3、10 variants 的墙钟时间和峰值内存。
2. 验证批次内并行度为 1，内存不会按“每策略复制完整原始表”线性失控；CausalDataView 可共享只读底层、隔离返回值。
3. 记录基线，不在没有业务 SLA 的情况下擅自设定激进阈值；若 10 variants 导致 OOM 或不可接受的数量级退化，则停止发布并定位复制点。
4. 验证取消延迟在一个已定义安全检查点内，且取消后文件仍可读。

**提交：** `test(fund-rotation): record batch performance baseline`

## Task 7：全量验证和回滚演练

**文件：**

- 修改：`docs/superpowers/plans/2026-08-02-fund-rotation-acceptance-record.md`

**步骤：**

1. 运行后端基金轮动全套、相关 API 测试、前端全套和生产构建。
2. 执行 `git diff --check` 和旧引用 `rg`；结果写入验收记录。
3. 在测试环境验证回退 Phase 6 切换提交后：旧写接口恢复、新内核文件仍在、历史运行仍可读；随后恢复 Phase 6。
4. 检查工作树只包含预期修改，不提交本地真实运行数据或敏感路径。
5. 使用 `requesting-code-review` 做最终独立 review，修复所有 P0/P1 后重复全量验证。

**最终验证：**

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation -q
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests -q
Set-Location frontend
npm test -- --run
npm run build
Set-Location ..
rg -n "fund-rotation/(backtests|defaults)|FundRotationConfig|run_fund_rotation_backtest" agent frontend scripts
git diff --check
```

预期：测试和构建全部通过；最后一次 `rg` 只允许命中明确标注的历史兼容测试/迁移说明，不得命中生产创建调用方。

**提交：** `chore(fund-rotation): complete strategy batch cutover`

## Phase 6 出口门禁

- 新创建路径唯一且无双模式切换。
- 历史 v1 运行只读可用。
- 后端、前端、真实数据、性能、取消和重启语义全部有证据。
- 最终 review 无未解决 P0/P1；回滚路径已实际演练。
