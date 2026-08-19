# Phase 3：相关性聚类代表 ETF 策略实施计划

> **执行要求：** 使用 `executing-plans` 技能实施。聚类、门禁和代表选择始终保留在该完整策略内部。

**目标：** 新增“相关性聚类后，每个入选簇只持有一个流动性最佳代表 ETF”的完整轮动策略，并产出足以解释每次选择的诊断。

**架构：** 策略内部依次执行收益准备、相关聚类、质量门禁、簇评分、medoid 邻域候选、流动性筛选、代表锁定/回退和目标权重生成。公共 Runner 只收到目标权重。

**技术栈：** Python、Pydantic、pandas、NumPy、SciPy/sklearn（沿用项目当前依赖）、pytest。

---

## Task 1：建立策略包和专用配置

**文件：**

- 新建：`agent/backtest/fund_rotation/strategies/correlation_representative/__init__.py`
- 新建：`agent/backtest/fund_rotation/strategies/correlation_representative/config.py`
- 新建：`agent/tests/fund_rotation/test_correlation_representative_config.py`

**专用配置默认项：** `k=8`、`top_n=3`、`correlation_lookback_weeks=52`、`momentum_window_weeks=4`、`recluster_interval_weeks=26`、`min_valid_weeks=20`、`min_pairwise_weeks=20`、`representative_candidate_count=5`、`representative_min_cluster_corr=0.85`、`representative_liquidity_window_days=20`、`representative_min_liquidity_observations=15`、`max_cluster_share_warn=0.50`、`max_cluster_share_reject=0.80`、`min_effective_cluster_count_warn=4.0`、`min_effective_cluster_count_reject=2.5`。字段名与设计 §4、Pydantic schema、规范化配置和 `resolved_config_hash` 完全一致，不提供旧别名。

**步骤：**

1. 写 schema/default/边界/extra-forbid 测试。
2. 明确门禁阈值属于本策略配置，不加入公共 ExecutionConfig 或 Runner。
3. 配置区分 reject 阈值和 warning 阈值；冲突组合启动前拒绝。
4. 生成稳定 JSON Schema 和默认配置。

**提交：** `feat(fund-rotation): define representative strategy config`

## Task 2：实现聚类和质量门禁内部模块

**文件：**

- 新建：`agent/backtest/fund_rotation/strategies/correlation_representative/clustering.py`
- 新建：`agent/backtest/fund_rotation/strategies/correlation_representative/gates.py`
- 新建：`agent/tests/fund_rotation/test_correlation_representative_gates.py`

**步骤：**

1. 为确定性、全同资产、单大簇、碎片簇、不完整配对和有效簇数边界写失败测试。
2. 沿用已确认的“不完整配对迭代排除”：反复移除造成配对历史不足的 ETF，直至剩余矩阵完整或不足以聚类；每次排除记录原因。
3. 聚类随机性必须使用配置 seed；相同输入输出簇标签规范化后完全一致。
4. 门禁输出 PASS/WARN/REJECT、稳定代码、阈值、实际值和受影响代码。
5. 门禁 REJECT 不返回决策动作 `INVALID`，而是输出 `SET_TARGETS`、`target_weights={}`、`cash_weight=1.0`、`reason_code=CLUSTER_QUALITY_REJECTED`、`quality_status=INVALID`；子运行技术上继续成功并生成完整现金净值与诊断。另写对照测试证明真正的决策动作 `INVALID` 会终止并使子运行失败。

**提交：** `feat(fund-rotation): add internal clustering quality gates`

## Task 3：实现 medoid 邻域候选

**文件：**

- 新建：`agent/backtest/fund_rotation/strategies/correlation_representative/representative.py`
- 新建：`agent/tests/fund_rotation/test_representative_selector.py`

**步骤：**

1. 写 medoid 测试：选择到簇内其他成员平均距离最小的真实 ETF，不使用虚构质心代码。
2. 按与 medoid 的相关性/距离稳定排序，取附近 M 只；并列按 ETF 代码稳定打破。medoid 只用于形成候选邻域，不作为最终相关性门槛的比较对象。
3. 对每个候选使用相同 PIT 窗口构造“排除该候选后的簇内等权指数”（leave-one-out cluster index），计算候选与该指数的相关系数；排除该相关性低于阈值、数据不足、不可交易和 fund_adj 历史不足候选。单成员簇按策略配置规定的门禁结果处理，不伪造相关系数。
4. 在剩余候选中按截至决策日可见的 ADV20 选择最大者；并列按代码稳定打破。
5. 诊断中保存 medoid、邻域距离排名、候选与 leave-one-out cluster index 的相关性、ADV20 和排除原因，确保两种指标不会共用含糊的 `correlation` 字段。

**提交：** `feat(fund-rotation): select liquid ETF near cluster medoid`

## Task 4：实现代表锁定和回退

**文件：**

- 修改：`agent/backtest/fund_rotation/strategies/correlation_representative/representative.py`
- 修改：`agent/tests/fund_rotation/test_representative_selector.py`

**步骤：**

1. 写测试：当前代表仍合格时维持锁定，避免仅因 ADV 小幅变化频繁换仓。
2. 写测试：代表停牌、退出候选或数据失效时，按已确认排序回退到下一候选。
3. 无候选时该簇不产生持仓，并记录 `NO_ELIGIBLE_REPRESENTATIVE`；该簇对应槽位保留现金，不返回决策动作 `INVALID`，也不把该槽位权重分配给其他簇。
4. 所有选择只使用决策日之前完成的数据，ADV 不得包含执行日。

**提交：** `feat(fund-rotation): lock and fallback cluster representatives`

## Task 5：组装完整策略 session

**文件：**

- 新建：`agent/backtest/fund_rotation/strategies/correlation_representative/strategy.py`
- 新建：`agent/tests/fund_rotation/test_correlation_representative_strategy.py`

**步骤：**

1. 实现 `resolve_requirements`，按配置声明周收益、复权、ADV 和预热需求。
2. session 内完成簇筛选和代表选择，每个入选簇最多输出一个 ETF。
3. 严格按设计 §8.3 形成权重：Top-N 每个槽位固定为 `1/Top-N`；合格簇或代表 ETF 不足时，对应槽位保留现金，不放大其他槽位。将有效槽位、空缺槽位及现金权重写入 decision metadata。
4. 纯预热不足时 Runner 不调 `evaluate()`；进入决策期后，聚类质量门禁拒绝返回清仓 `SET_TARGETS` 并标记 `quality_status=INVALID`，只有必要数据缺失或策略不变量破坏才返回决策动作 `INVALID`。
5. finalize 输出簇映射、门禁、代表候选、调仓决策和排除日志等策略专属产物。

**提交：** `feat(fund-rotation): add complete correlation representative strategy`

## Task 6：注册 Catalog 并做研究冒烟

**文件：**

- 修改：`agent/backtest/fund_rotation/catalog.py`
- 修改：`agent/tests/fund_rotation/test_strategy_catalog.py`
- 新建：`agent/tests/fund_rotation/test_correlation_representative_integration.py`

**步骤：**

1. 显式注册新策略，测试 descriptor、schema、默认值、源码哈希和 requirements。
2. 用合成数据端到端断言：持仓数不超过入选簇数，每簇至多一只，成交遵守 ETF 费用/100 份/ADV20 容量。
3. 用本地 `bac86bdddcf85601` 对应的数据快照做只读研究冒烟；不要把运行目录本身当输入数据源。
4. 对比旧“簇内全部等权”结果只作诊断，不把收益更高作为验收条件；验收关注因果性、交易可行性和可解释性。
5. 运行全套基金轮动测试。

**验证：**

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation\test_correlation_representative_config.py agent\tests\fund_rotation\test_representative_selector.py agent\tests\fund_rotation\test_correlation_representative_integration.py -q
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation -q
git diff --check
```

**提交：** `test(fund-rotation): verify representative strategy integration`

## Phase 3 出口门禁

- 新策略只通过完整策略契约接入。
- 公共层没有 Clusterer/Gate/Selector 插件接口或策略 ID 分支。
- 每次代表选择和门禁结果都能从产物复算。
- 端到端运行满足最多每簇一只 ETF 和因果 ADV20 规则。
