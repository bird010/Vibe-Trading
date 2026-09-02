# R81 组合策略研究报告

## 结论

本轮在冻结快照 `7596807626fdf7f1aa9bdaddd84cd4575e15ac473c8331879d841ecacd941de6` 上完成了 R58 优先对照和三个正交组合方向的正式双变体回测。没有候选满足全部 Champion 门槛，因此最终状态为 `NOT_QUALIFIED`，不进入部署或实盘。

按风险收益排序，R85 是当前最好的研究候选：R81 动态经济角色代表 + R74 正动量/60 日波动率排序。它相对 R58 显著提高年化收益和 Sharpe，但全区间最大回撤仍恶化超过允许的 1 个百分点；因此只能保留为未晋级研究候选。

## 固定口径

- 正式区间：`20130329..20220729`；确认区间 `20220801..20260801` 未用于选择。
- 5 个滚动折：Train156 / Validation52 / Test52 / Step52。
- 执行合同：初始资金 100 万、佣金 0.00025 且最低 5、参与率 0.05、ADV20/10、滑点 5–30bps、整手100。
- 每个批次恰好两个变体，模式为 `RESEARCH_ONLY`，共享同一快照和执行规则。

## 全区间结果

| 变体 | 年化收益 | Sharpe | 年化波动 | 最大回撤 | 一向年化换手 | 门槛 |
|---|---:|---:|---:|---:|---:|---|
| R58 基准 | 3.63% | 0.492 | 7.38% | -19.24% | 5.33 | 基准 |
| R82：R81代表 + R57 | 14.65% | 0.963 | 15.21% | -30.49% | 9.67 | 未通过回撤 |
| R83：R82 + R77防御相对动量 | 14.64% | 0.962 | 15.22% | -30.49% | 9.74 | 未通过回撤 |
| R84：R82 + R62真实逆波动 | 12.61% | 1.009 | 12.50% | -22.46% | 10.38 | 未通过回撤 |
| R85：R81代表 + R74波动调整排序 | 14.38% | 1.213 | 11.86% | -22.69% | 14.64 | 未通过回撤 |

Champion 门槛为：validation Sharpe 更高、收益不低、最大回撤恶化不超过 1 个百分点、过半折胜出且所有数据/因果/执行门槛通过。R85 在测试折收益和 Sharpe 上胜出 4/5 折，但最大回撤 5/5 折劣于 R58；因此不能晋级。

## 组合方向判断

- R57 信号是有效的收益增强方向，但单独组合带来明显尾部风险。
- R77 防御层在当前 R81 角色组合中几乎没有改变现金防御敞口，未产生实质增益。
- R62 真实逆波动有效降低波动和回撤，但仍未达到 R58 的回撤门槛。
- R74 方向产生最佳 Sharpe/Calmar，但换手最高，且回撤仍超门槛。
- R75 固定 15% 总波动目标没有形成新的可证伪假设：R84/R85 已把波动压到约 12%，在不调参的冻结合同下该层预期不会改善尾部回撤，因此不为填轮次强行叠加。

## 可追溯产物

- [研究设计](../specs/2026-09-03-fund-rotation-r81-combination-design.md)
- [执行计划](../plans/2026-09-03-fund-rotation-r81-combination-plan.md)
- [R82 实现](../../../agent/backtest/fund_rotation/strategies/ai_rotation_r82_economic_role_dynamic_rep_r57_signal/strategy.py)
- [R83 实现](../../../agent/backtest/fund_rotation/strategies/ai_rotation_r83_r81_r57_r77_combo/strategy.py)
- [R84 实现](../../../agent/backtest/fund_rotation/strategies/ai_rotation_r84_r81_r57_r62_combo/strategy.py)
- [R85 实现](../../../agent/backtest/fund_rotation/strategies/ai_rotation_r85_r81_r74_combo/strategy.py)
- 实验目录：`agent/runs/fund_rotation/experiments/fund_rotation_r81_combinations_20260903_v2/`
- 正式批次：`6d6feb164db2`、`eb29d38020ea`、`934fe3522c7e`、`f1c1e5d35cc3`

原始批次、失败批次和 ledger 均保留；没有自动部署、修改既有 R81/R58 行为或使用确认区间收益。
