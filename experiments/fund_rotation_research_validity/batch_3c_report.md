# Batch 3C：波动率调整动量排名

- 策略：`ai_rotation_r74_r39_vol_adjusted_score`
- 控制组：`ai_rotation_r39_incumbent_carry`
- 唯一机制变化：正的 R39 cluster momentum 除以年化 `volatility_60`；不改变仓位权重
- 近零波动率 fail-closed 阈值：`1e-8`
- manifest SHA-256：`d0f74e54900711f0344d64b72f10ef898091bf9fb7fa9f7c05d7d24222310e40`
- 实验状态：`UNAVAILABLE_INPUTS`
- promotion_allowed：`false`

## 证据边界

R74 只在 R39 的 cluster ranking 层使用 signal cutoff 前可见的 60 个日收益标准差，年化因子固定为 252；正的 R39 momentum 才有资格形成 momentum/volatility_60 分数。聚类、代表基金、Top-K、固定槽位、staging、incumbent carry、执行和成本语义均保持不变，不引入 inverse-volatility allocation。

当前没有可验证冻结 U1、R39 paired control、三折回测和因果行情输入，故 volatility_60/momentum/score 覆盖率、排名变化、持有期、switch、换手、阻塞率、fold contribution、CAGR/Sharpe/MDD/Calmar、CVaR、worst 3M、成本后收益及 normal/2x/T+1/T+2 全部为`unavailable`，没有用零值替代或声称晋级。

## 最小改动自评

仅新增 R74 score-only overlay、显式注册、focused tests、Batch 3C 登记脚本和本中文报告；未修改 R39/R40、Runner、execution ledger、平台架构或历史实验产物。
