# StockPred Cohort R3 修复设计

## 目标

修复 `ccce9a8` 审查中确认的核心数值错误，并补齐上一轮尚未闭环的高优先级执行、数据质量、产物和前端契约。保持 `signal_cohort_v1` 的固定资金、独立 cohort 语义，不扩展为真实组合资金共享回测。

## 不可变约束

1. 执行成交、涨跌停判断、ADV、费用和现金全部使用未复权人民币价格。
2. 复权价格只用于 raw label、固定期限收益率和相对收益比较，不与真实股数直接相乘。
3. Horizon 在任何退出前冻结，使用目标退出日复权开盘收益率；清算路径不得污染固定期限口径。
4. Liquidation 对残仓使用最后有效真实价格，扣预计卖出费用和流动性折价；清算匹配基准使用初始投入本金比例。
5. 复权数据缺失不得填充自其他股票或未来记录；覆盖不足必须 fail-closed。
6. 所有计划评估日必须形成可审计记录；数据失败收益为 null，不伪造成 0，也不能静默从覆盖率分母消失。
7. Process 与 in-process、Graph 与 Alpha 的 Cohort 路径必须使用同一语义。
8. 所有生产代码修改必须先有能够正确失败的测试，再做最小实现。

## 核心数据流

```text
候选信号
  -> 信号日 universe / 名称 / 原始行情 / 复权覆盖门禁
  -> 冻结过滤后 SignalSnapshot
  -> T+1 原始开盘执行
  -> 退出前 horizon 快照
       原始入场名义金额 × adj_open(target) / adj_open(entry)
  -> 原始开盘执行退出
  -> 残仓 terminal valuation
  -> target-horizon benchmark + liquidation-matched benchmark
  -> 原子产物发布与前端读取
```

## 任务边界

### 任务一：核心货币、估值与基准

- 复用现有 `apply_qfq`，禁止自定义填充。
- ExecutionPolicy 始终使用原始 `open`。
- 保存不可变的初始成交成本，退出基准权重在账本变更前确定。
- Horizon 使用复权开盘收益率作用于原始入场名义金额。
- 残仓接入 `ValuationPolicy.terminal_value()`，并产生 terminal benchmark event。

### 任务二：数据质量、失败语义与编排

- Eligibility 使用信号日行情并检查停牌、上市/退市状态和复权覆盖。
- 截断、行情不足、空信号和合法空目标均产生明确 cohort 记录。
- 修复拒单 fill-rate、事件 cohort_id 和账本不变量。
- Process/in-process 共用 Cohort 执行路径；schema 使用原子写入。
- PIT 使用策略声明的实际依赖，unknown 依赖降级为 `snapshot_only`。

### 任务三：产物、API 与前端闭环

- 海选阶段为全量信号股票生成扩展窗口 OHLCV。
- Chart manifest 路径必须限制在版本目录内并校验哈希。
- JSON 输出统一清洗 NaN/Inf。
- 增加股票列表、时期稳定性和个股 K 线买卖点读取闭环。
- Legacy 报告显式分流并提示旧组合口径。

## 验收

- 每个修复项都有 RED 与 GREEN 证据。
- Cohort 定向后端测试、StockPred 后端回归、TypeScript 和前端测试完成。
- 新增数值测试至少覆盖：全额退出上涨一倍、复权因子跨股票缺失、除权坐标、目标日开盘、残仓折价、持有期截断、全拒单、process/in-process 路由。
- 不把当前分支已有的无关失败冒充为本次修复成功。

