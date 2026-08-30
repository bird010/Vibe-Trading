# Batch 2：容量感知代表回退报告

- 策略 ID：`ai_rotation_r71_r39_capacity_aware_representative`
- 实验状态：`unavailable`
- U1 状态：`unavailable`
- manifest：`E:\code\stock\Vibe-Trading\experiments\fund_rotation_research_validity\batch_2\manifest.json`
- manifest SHA-256：`7d7dc7d03c063c0f0f880b4a3d140f9edc739687eaf8c035edb97807c917aabe`

## 规则与审计边界

R71 继承 R39；只有决策 cutoff 可见、同簇同身份且容量证据明确足够时才解锁代表，否则按确定性候选顺序回退，全部不可用时进入现金。未知或未来成交量不被当作可用容量。

## 主指标

blocked attempt ratio、parent order fill ratio、capacity-zero 次数、未成交机会成本、成交后换手率和目标偏离持续天数为主指标；CAGR、Sharpe、MDD 仅作护栏。1×/2×/3× 成本与容量压力测试必须使用同一冻结 U1 和账户语义。

## 当前证据

`缺少冻结 U1 manifest、历史反事实诊断或正式 paired backtest 输入：frozen_u1_manifest, historical_counterfactual, paired_backtest`。本次没有把缺失证据填成零，也没有生成虚假的收益、成本或晋级结论；正式 U1 paired backtest 和历史反事实完成前，`promotion_allowed` 保持 `false`。
