# 基金轮动策略热插拔：整体实施工作流

> **执行要求：** 使用 `executing-plans` 技能逐阶段实施；每一阶段必须通过本文件定义的出口门禁后，才能进入下一阶段。

**目标：** 在不破坏现有基金轮动回测及历史运行只读能力的前提下，把基金轮动改造成“完整策略热插拔、统一公共执行、公平批量比较、RESEARCH_ONLY”的系统。

**架构原则：** 公共框架只负责数据快照、因果数据访问、日历、执行、估值、指标、批次编排和持久化；完整 `FundRotationStrategy` 负责形成目标权重。聚类器、聚类门禁和簇内 ETF 选择器均属于相关性策略内部实现，不成为公共插件接口。

**技术栈：** Python、Pydantic、pandas、Lance、FastAPI、pytest、React、TypeScript、Vitest。

**设计依据：** `docs/superpowers/specs/2026-08-02-fund-rotation-strategy-plugin-design.md`

---

## 1. 第一性原理与不可变约束

### 1.1 基本事实

1. 不同轮动方法未必使用聚类，因而公共接口不能暴露聚类步骤。
2. 策略优劣只有在相同数据版本、标的池、交易日历、执行规则、费用和评价区间下才可比较。
3. 周频信号仍由日频执行器在下一可交易日执行；不得使用执行日收盘后才能知道的数据。
4. 日终净值序列的首点不是初始本金。指标计算必须额外使用正式区间开始前的 `initial_nav=1.0`。
5. 当前系统已有历史 JSON/CSV 运行产物，迁移不能破坏其只读能力。
6. 当前阶段仅服务研究，不产生实盘指令，不开放自动交易链路。

### 1.2 不可变约束

- 所有子运行共享同一个不可变数据快照和正式评价日历。
- 策略只能通过 `CausalDataView` 读取已声明且当时可见的数据。
- 公开插件单位只有完整策略；每个策略的专用参数由其 Pydantic 配置模型定义。
- 执行语义只有目标权重，不保留方向信号/目标权重双模式切换。
- `HOLD_TARGETS` 在不存在历史目标时等价于持有现金。
- 纯数据预热阶段不调用 `evaluate()`；满足预热契约后，任何被调用的 `evaluate()` 返回 `INVALID` 都终止该子运行，包括正式评价开始前的决策日。
- 首版（Phase 4 建立批次服务）在批次内部按稳定 `variant_key` 顺序执行；服务层只允许有限个父批次并发，默认 1。
- 不支持断点续跑；服务重启后将未完成批次标记为 `FAILED_INTERRUPTED`，重新运行必须使用新幂等键。
- 结果和 UI 必须显式标注 `RESEARCH_ONLY`。

## 2. 目标工作流

```mermaid
flowchart LR
    A["提交策略批次"] --> B["校验幂等键与策略配置"]
    B --> C["固定 Lance 版本、ETF 池、交易日历和执行参数"]
    C --> D["构建公平比较指纹"]
    D --> E["按顺序创建策略会话"]
    E --> F["通过 CausalDataView 逐决策日生成目标权重"]
    F --> G["统一执行器：下一交易日成交、容量与费用约束"]
    G --> H["统一估值与 initial_nav 指标计算"]
    H --> I["写入子运行产物和策略诊断"]
    I --> J{"还有策略？"}
    J -- 是 --> E
    J -- 否 --> K["严格按共同 evaluation_calendar 生成比较结果"]
    K --> L["JSON/CSV 持久化、SSE 和前端展示"]
```

## 3. 阶段依赖

| 阶段 | 目标 | 依赖 | 主要出口 |
|---|---|---|---|
| Phase 0 | 修正公平比较地基 | 无 | 快照、52 周边界、评价日历、初始净值语义正确 |
| Phase 1 | 定义策略契约和 Catalog | Phase 0 | 策略可发现、配置可校验、实现可快照 |
| Phase 2 | 提取公共 Runner 和基准策略 | Phase 1 | 基准策略经新 Runner 与批准差异外保持一致 |
| Phase 3 | 实现相关性聚类代表 ETF 策略 | Phase 2 | 新策略作为完整插件接入并产出诊断 |
| Phase 4 | 批次后端、API、持久化 | Phase 2、3 | 多策略公平批次可提交、取消、恢复判定和读取 |
| Phase 5 | 前端动态配置与比较 | Phase 4 | 用户可配置、运行和对比不同完整策略 |
| Phase 6 | 切换、清理和总验收 | Phase 0–5 | 所有调用方迁移，旧写接口删除，历史只读保留 |

## 4. 跨阶段工作规则

### 4.1 每个任务的固定循环

1. 写一个只针对当前行为的失败测试。
2. 运行该测试并确认失败原因与预期一致。
3. 做最小实现，不顺手重构相邻代码。
4. 运行目标测试和 `agent/tests/fund_rotation` 全套测试。
5. 对照设计文档检查接口、产物和错误码。
6. 单独提交可回退的 commit；禁止提交处于已知失败状态的中间节点。

### 4.2 兼容策略

- Phase 0–5 保留现有公开创建回测接口，新的 Runner 和批次路径先通过内部测试验证。
- 不向用户提供“新旧模式”开关。
- Phase 6 同一提交组内完成前端/调用方切换和旧 POST 接口删除。
- 历史 v1 `state.json`、事件和产物永久只读兼容；不把旧运行升级重写成新格式。

### 4.3 公共验证命令

在 `E:\code\stock\Vibe-Trading` 执行：

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation -q
```

若环境名称不同，先使用项目当前测试解释器，但不得用另一个 pandas 版本替代正式验证环境。

前端阶段执行：

```powershell
Set-Location frontend
npm test -- --run
npm run build
```

## 5. 阶段门禁

### Phase 0 出口

- 每次运行在读取前固定明确的 Lance 版本、ETF 池和交易日历。
- 52 个有效周收益窗口边界有直接单元测试；端到端测试有足够的下一交易日。
- 所有相关 `pct_change` 均显式使用 `fill_method=None`。
- 正式评价日历索引严格相等；不得用日期交集缩短区间。
- 日终净值不伪造首日 1.0，指标以独立 `initial_nav=1.0` 计算首期收益。
- 预评价目标在正式区间首个交易日执行，且真实、理想、买入持有三条路径语义一致。
- 预先保存的 golden 结果仅出现批准清单内的差异。

### Phase 1 出口

- Catalog 为显式白名单，未知/重复策略 ID 启动失败。
- 每个策略暴露描述信息、JSON Schema、默认配置和配置校验。
- 数据需求由“策略 + 配置”解析，未声明字段访问会被运行时拒绝。
- 策略源码、框架源码、配置和数据快照均有稳定哈希。
- Catalog 的 `resolve()` 完成默认值补齐、接口版本校验、数据需求解析和启动时实现快照绑定，返回不可变 `ResolvedStrategyBinding`。

### Phase 2 出口

- 公共 Runner 不含聚类专用分支。
- 基准策略仅通过目标权重契约驱动公共执行器。
- 现有结果除 Phase 0 批准差异外逐调仓日一致；浮点比较遵循计划规定容差。
- 策略专属产物通过声明式 artifact roles 发布。

### Phase 3 出口

- 相关性策略内部完成聚类、质量门禁、medoid 邻域候选和 ADV20 代表选择。
- 每个入选簇最多持有一个代表 ETF；候选、排除、锁定和回退原因均可审计。
- 门禁阈值只属于该策略配置，不泄漏到公共 Runner。

### Phase 4 出口

- 同一幂等键和同一规范化请求返回已有批次；载荷不同返回 409。
- 批次先按各 Variant 的最近预评价决策日回溯 warmup，取最早数据起点，只固定一次公共快照，并按稳定 `variant_key` 顺序执行子运行。
- SSE 使用批次级严格递增的全局 `seq`，`Last-Event-ID` 只对应该序号；断线可续读事件但不续跑计算。
- 成功子运行的净值索引必须与 `evaluation_calendar` 完全相等后才参与比较。
- 用户主动取消进入 `CANCELED`，服务异常中断进入 `FAILED_INTERRUPTED`；两者都不支持断点续跑。

### Phase 5 出口

- UI 从 Catalog schema 生成策略参数表单，不硬编码策略专用字段。
- 支持多个策略变体、进度、失败隔离、指标和净值对比。
- K 线图能核对买卖日期、方向、数量、价格和阻塞原因。
- 页面和导出物显式展示 `RESEARCH_ONLY`。

### Phase 6 出口

- `rg` 证明没有旧创建接口和旧配置对象的调用方。
- 旧 POST/defaults 接口删除，旧 GET 历史读取仍通过兼容测试。
- 后端全套、前端测试与构建、1/3/10 策略性能测试和真实本地数据冒烟全部通过。
- 回滚只需回退 Phase 6 切换提交，不破坏 Phase 0–5 已验证内核。

## 6. 计划文件索引

- `docs/superpowers/plans/2026-08-02-fund-rotation-phase0-comparison-foundation.md`
- `docs/superpowers/plans/2026-08-02-fund-rotation-phase1-strategy-contracts-catalog.md`
- `docs/superpowers/plans/2026-08-02-fund-rotation-phase2-common-runner-baseline.md`
- `docs/superpowers/plans/2026-08-02-fund-rotation-phase3-correlation-representative.md`
- `docs/superpowers/plans/2026-08-02-fund-rotation-phase4-batch-backend.md`
- `docs/superpowers/plans/2026-08-02-fund-rotation-phase5-frontend-comparison.md`
- `docs/superpowers/plans/2026-08-02-fund-rotation-phase6-cutover-acceptance.md`

## 7. 停止条件

出现以下任一情况时停止推进下一阶段，先修正文档或实现：

- 需要改变已确认的交易时点、费用、容量、目标权重或评价口径。
- 为兼容某个策略而准备在公共 Runner 中增加策略名判断。
- 无法证明子运行共享完全相同的公共数据快照。
- 需要让策略直接访问原始 Lance/DataFrame 才能实现。
- golden 差异超出 Phase 0 明示允许清单。
- 新接口尚未覆盖现有调用方，却准备删除旧接口。
