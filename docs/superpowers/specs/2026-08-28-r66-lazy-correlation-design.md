# R66 Lazy Correlation ETF 轮动设计

## 目标

新增 R66 作为 R64 的行为等价优化版本，保留 R64 的策略语义、历史可复现性和审计字段，同时避免预先计算不会被选择器使用的大量 ETF 两两相关性。

## 不变约束

- R64 和 R65 的实现与结果不改动。
- R66 仍使用 R57 三因子：`bias=0.3`、`slope=0.3`、`efficiency=0.4`。
- 仍使用 `raw_slope_25d > 0` 正斜率过滤。
- 仍使用 52 周收益、最少 20 周有效观察、严格 `corr < 0.80`。
- 仍是按排序顺序贪心选择最多 3 只 ETF。
- 相关性过高时不提前退出；只有相关性不可用时沿用现有控制流立即停止该候选的检查。
- 仍使用固定槽位等权、半仓再入场和 incumbent carry。

## 方案

R66 复制 R64 的决策流程，但将相关性输入改为 `PairwiseCorrelationLookup`：

1. `lookup(a, b)` 首次被选择器请求时，继续使用原来的 `returns[[left, right]].dropna()` 和 `Series.corr()` 计算。
2. 结果按排序后的 pair key 缓存，重复请求不重复计算。
3. R66 选择器只请求当前候选与已选 ETF 的相关性；达到 3 只后立即结束。
4. diagnostics 只记录实际使用的相关性和实际拒绝的候选，字段结构与 R64 保持一致。

R66 同时使用两项安全的数据路径优化：

- `CausalDataView.returns()` 的 weekly 分支先调用 `compute_weekly_returns()`，避免先做一次不会被使用的完整 adjusted-close 计算。
- R66 的因子行计算将行情与复权数据按 `ts_code` 预分组，避免循环内对全表重复布尔扫描；因子公式和输出字段不变。

## 结果等价性

新增测试覆盖：

- lazy selector 与原 selector 在阈值边界、缺失观察、缺失列、负相关和诊断优先级上的一致性；
- R66 固定上下文下与 R64 的目标权重、现金权重、原因、选择结果和相关性诊断一致；
- lookup 的调用次数只包含选择器实际需要的 pair；
- weekly returns 不再重复调用完整 adjusted-close，但数值结果保持一致。

## 性能预期

相关性阶段由预计算 `M(M-1)/2` 个 pair 降为选择器实际访问的 pair，R64 的 `top_n=3` 下通常接近线性候选扫描；同时减少 weekly 复权和因子数据扫描的重复工作。

