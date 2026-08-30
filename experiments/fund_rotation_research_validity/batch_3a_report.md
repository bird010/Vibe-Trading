# Batch 3A：R126d 绝对动量门

- 策略：`ai_rotation_r72_r39_absolute_momentum`
- 控制组：`ai_rotation_r39_incumbent_carry`
- 唯一机制变化：`R126d > 0`；失败候选只进入现金
- manifest SHA-256：`bc0d544097c9b301c18ba65e1dd005d5d926074553f9cdabf50f631bb00b7328`
- 实验状态：`UNAVAILABLE_INPUTS`
- promotion_allowed：`false`

## 证据边界

R72 仅在 R39 已生成目标之后读取 signal cutoff 可见的 adjusted close，严格计算 126 日收益；缺失窗口、非有限值、零收益和负收益分别记录，失败目标释放为现金，不重排或重分配幸存目标。

当前没有可验证冻结 U1、R39 paired control、三折回测和因果行情输入，故历史负趋势频率、前瞻负趋势频率、三折 CAGR/Sharpe/MDD、CVaR、worst 3M、现金占比、switch、持有期、换手、成本后收益以及 normal/2x/3x 与 T+1/T+2 场景全部为`unavailable`，没有用零值替代或声称晋级。

覆盖率（历史负趋势、前瞻结果、paired control、三折和候选观测）全部为`unavailable`，不将缺失数据解释为零覆盖或零风险。

参数邻域、fold contribution 和阻断证据字段均明确记录为 `unavailable`；因此不执行参数搜索、不计算 fold 晋级贡献，也不允许晋级。

## 最小改动自评

仅新增 R72 overlay、显式注册、focused tests、Batch 3A 登记脚本和本中文报告；未修改 R39/R40、Runner、execution ledger、平台架构或历史实验产物。
