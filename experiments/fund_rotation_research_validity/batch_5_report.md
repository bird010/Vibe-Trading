# Batch 5：风险层与防御资产

- 固定目标波动率：`0.15`；exposure=min(1,target/σ)，不使用杠杆
- 防御比较：现金基线、固定短债、冻结防御池相对动量；不做历史最优资产回填
- breadth：只按独立 U1 identity 计数
- manifest SHA-256：`d58a183562aef87a11f057001c1c76a0fe98e31c9f777feb940beabfb4d6d22c`
- 实验状态：`UNAVAILABLE_INPUTS`
- promotion_allowed：`false`

## 证据边界

R75 只增加一个固定目标波动率风险层，缺失或非正组合波动率时 fail-closed 到现金；R76 是现金防御基线；固定短债和 R77 防御相对动量分别作为防御层比较臂。首轮不与绝对动量组合，不使用杠杆，也不把事后表现最好的防御资产回填历史。

当前缺少冻结 U1、四臂相同快照的 paired backtest、三折因果数据和防御池历史，因此 Calmar、MDD、现金占用、防御换手、fold contribution、CAGR/Sharpe 及 normal/2x/T+1/T+2 全部为 `unavailable`，不作风险层晋级结论。

## 最小改动自评

仅新增纯风险层、三个薄策略适配器、显式注册、focused tests、Batch 5 登记脚本和本中文报告；未修改 R39/R40、公共 Runner、execution ledger 或历史实验产物。
