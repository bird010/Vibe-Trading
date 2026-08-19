# Phase 5：前端动态策略配置与比较实施计划

> **执行要求：** 使用 `executing-plans` 技能实施。前端不得硬编码相关性策略专用参数；参数 UI 由 Catalog schema 驱动。

**目标：** 在 StockPred 的“基金轮动”页签中配置多个完整策略变体、运行批次、观察进度、比较净值/指标，并在 K 线图核对买卖时点和数量。

**架构：** React hook 管理 Catalog、草稿 variants、批次状态和 SSE；通用 schema form 只支持后端当前会生成的 JSON Schema 子集，对不支持 schema 显式报错。策略诊断作为通用 artifact tabs 展示。

**技术栈：** React、TypeScript、Vitest、Testing Library、现有图表组件。

---

## Task 1：建立前端领域类型和 API 客户端

**文件：**

- 新建：`frontend/src/components/stockpred/fund-rotation/types.ts`
- 新建：`frontend/src/components/stockpred/fund-rotation/api.ts`
- 新建：`frontend/src/components/stockpred/fund-rotation/__tests__/api.test.ts`

**步骤：**

1. 定义 Catalog descriptor/schema、`mode: "RESEARCH_ONLY"` 的 variant request、设计规定的 batch/child state 枚举、统一 event envelope 和 artifact manifest 类型。
2. API 客户端覆盖策略列表/详情、提交、查询、取消、父批次事件和既有子运行只读端点；K 线使用 `GET /stockpred/fund-rotation/backtests/{run_id}/instruments/{ts_code}/chart`，不在 hook 中散落 URL。
3. 运行时检查 `schema_version`，遇到未知主版本显示不兼容错误，不静默猜字段。
4. 所有创建请求生成并持久保留幂等键，网络重试复用同一键。

**提交：** `feat(frontend): add fund rotation batch client`

## Task 2：实现受限 JSON Schema 参数表单

**文件：**

- 新建：`frontend/src/components/stockpred/fund-rotation/StrategyConfigForm.tsx`
- 新建：`frontend/src/components/stockpred/fund-rotation/__tests__/StrategyConfigForm.test.tsx`

**步骤：**

1. 为 string/number/integer/boolean/enum、默认值、required、minimum/maximum 和嵌套 object 写组件测试。
2. 表单值初始化自 Catalog defaults；切换策略时创建新配置，不把旧策略专用字段带过去。
3. 客户端做即时格式验证，但提交前以后端 Pydantic 校验为最终权威。
4. 对 array、oneOf 等当前未支持 schema 显示“暂不支持该配置结构”，禁止丢字段提交。
5. 显示字段中文说明、单位和默认值来源。

**提交：** `feat(frontend): render strategy config from catalog schema`

## Task 3：实现多变体编辑器

**文件：**

- 新建：`frontend/src/components/stockpred/fund-rotation/StrategyVariantsEditor.tsx`
- 新建：`frontend/src/components/stockpred/fund-rotation/__tests__/StrategyVariantsEditor.test.tsx`

**步骤：**

1. 支持添加、复制、删除和命名 variant；至少保留一个。
2. 同一 strategy_id 可添加多个不同配置；前端草稿可使用仅 UI 内部的临时 key，但提交身份由后端根据 `strategy_id + "@" + resolved_config_hash[:12]` 生成 `variant_key`，展示标签和拖动顺序不参与身份。
3. 公共参数（评价区间、初始资金、执行规则）只编辑一次；策略专用参数位于卡片内。
4. 提交摘要清楚列出策略版本和修改过的专用参数。
5. 页面显式显示 `RESEARCH_ONLY`，不得出现实盘执行按钮。

**提交：** `feat(frontend): edit complete strategy variants`

## Task 4：迁移 hook 到批次状态和 SSE

**文件：**

- 修改：`frontend/src/components/stockpred/fund-rotation/useFundRotation.ts`
- 新建：`frontend/src/components/stockpred/fund-rotation/__tests__/useFundRotation.test.tsx`

**步骤：**

1. 测试单/多 variant 提交都调用统一 strategy-batches POST。
2. 保存最后处理的父批次全局 `seq`，SSE 重连通过 `Last-Event-ID` 从下一序号继续；重复事件去重，未知 `event_type/stage/strategy_substage` 安全降级为原文展示或忽略扩展字段。
3. 按统一 envelope 中的 `scope/run_id/variant_key/strategy_id/stage/strategy_substage/progress` 显示父批次和每个子运行状态及失败原因；状态名严格使用设计枚举，包括 `COMPUTING_METRICS`、`WRITING_RESULTS`、`SUCCEEDED`、`PARTIAL_SUCCEEDED` 和 `CANCELED`。
4. 安全取消后继续读取终态；不得显示“稍后续跑”。
5. 页面刷新通过 batch GET 恢复展示，计算本身不恢复。

**提交：** `feat(frontend): track fund rotation strategy batches`

## Task 5：实现公平比较视图

**文件：**

- 新建：`frontend/src/components/stockpred/fund-rotation/StrategyComparison.tsx`
- 新建：`frontend/src/components/stockpred/fund-rotation/__tests__/StrategyComparison.test.tsx`

**步骤：**

1. 展示共同评价区间、比较指纹、纳入/排除策略及原因。
2. 净值曲线只使用后端 comparison equity，不在浏览器自行取日期交集。
3. 指标表显示总收益、年化、波动、夏普、最大回撤、换手和成本；字段缺失显示 N/A。
4. 子运行失败不阻断成功策略展示，且不能误显示为 0 收益。
5. 支持从比较行进入子运行详情。

**提交：** `feat(frontend): compare fund rotation strategies fairly`

## Task 6：在 K 线图显示买卖点、数量和时机

**文件：**

- 新建：`frontend/src/components/stockpred/fund-rotation/TradeMarkersChart.tsx`
- 新建：`frontend/src/components/stockpred/fund-rotation/__tests__/TradeMarkersChart.test.tsx`
- 修改：`frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx`

**步骤：**

1. 通过子运行 chart API 获取数据，以 executed fills 为实心买卖标记，以 blocked orders 为描边/叉号；目标产生日期可用淡色垂线，避免把信号日误认为成交日。
2. tooltip 显示 ETF、名称、方向、下单日期、成交日期、数量、价格、金额、费用、目标权重和阻塞原因。
3. 同日多笔交易使用纵向错位或聚合弹层，不遮蔽 K 线。
4. 价格序列缺失时仍显示订单表，并明确“无可用 K 线”，不能凭订单价格合成蜡烛图。
5. 测试买卖数量、日期时区和 signal/execute 两日期不混淆。

**提交：** `feat(frontend): visualize ETF trade timing and quantities`

## Task 7：整合基金轮动页签并验收

**文件：**

- 修改：`frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx`
- 修改：`frontend/src/components/stockpred/__tests__/FundRotationTab.test.tsx`
- 修改：`frontend/src/pages/StockPred.tsx`

**步骤：**

1. 形成“公共参数 → 策略变体 → 提交/进度 → 比较 → 子运行诊断/K线”的独立交互，不复用批量筛选排名组件。
2. 默认加载 baseline，可添加 correlation representative；策略列表完全来自 Catalog。
3. 覆盖 Catalog 加载失败、配置 422、幂等 409、部分失败、取消、SSE 重连和历史批次只读。
4. 使用 mock API 做完整用户流测试，并构建生产包。

**验证：**

```powershell
Set-Location frontend
npm test -- --run
npm run build
```

**提交：** `test(frontend): verify fund rotation comparison workflow`

## Phase 5 出口门禁

- 策略专用参数零硬编码，未知 schema 不静默降级。
- 单策略和多策略共用同一提交工作流。
- 比较、失败隔离、取消和 SSE 重连均有组件测试。
- K 线标记能区分信号、订单、成交和阻塞。
