# Batch 3B：多周期相对动量排名

- 策略：`ai_rotation_r73_r39_multi_horizon_rank`
- 控制组：`ai_rotation_r39_incumbent_carry`
- 唯一机制变化：等权 `rank(R60) + rank(R120) + rank(R240)`，不加入 R20
- manifest SHA-256：`3e57d9b4c00036dc697d9c03fe0e48ca004e5b96d3f27dab7d9f63a92052f22a`
- 实验状态：`UNAVAILABLE_INPUTS`
- promotion_allowed：`false`

## 证据边界

R73 只替换 R39 的 cluster ranking score；选中的槽位、代表基金、staging、incumbent carry、执行和成本语义保持不变。每个周期先在 signal cutoff 可见的adjusted close 上计算相对收益，再对 cluster 做确定性降序排名，ties 使用最小成员代码；只有三个周期均有分数的 cluster 才可聚合。

当前没有可验证冻结 U1、R39 paired control、三折回测和因果行情输入，故 R60/R120/R240 覆盖率、周期排名相关性、rank flip、持有期、switch、换手、fold contribution、CAGR/Sharpe/MDD、CVaR、worst 3M、成本后收益及 normal/2x/T+1/T+2 全部为`unavailable`，没有用零值替代或声称晋级。

参数邻域固定为三个等权周期，不执行事后搜索；blocking evidence 和 fold contribution均显式记录为 `unavailable`。

## 最小改动自评

仅新增 R73 score-only overlay、显式注册、focused tests、Batch 3B 登记脚本和本中文报告；未修改 R39/R40、Runner、execution ledger、平台架构或历史实验产物。
