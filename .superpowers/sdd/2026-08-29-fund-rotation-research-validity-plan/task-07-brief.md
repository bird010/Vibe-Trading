# Task 7 实施简报：Batch 3C 波动率调整排名

## 目标

严格执行文档 5C：以 R39 为控制组，仅将 cluster 排名分数替换为
`momentum / volatility_60`；固定单一无自由参数规则，不引入绝对动量、inverse-vol allocation、
新仓位权重或防御资产。

## 最小改动约束

- 新增独立 R74 策略 ID、focused tests、Batch 3C 登记脚本和中文报告。
- R39 的聚类、代表基金、Top-K、固定槽位、staging、incumbent carry、执行和成本语义保持不变。
- `volatility_60` 只使用 signal cutoff 前可见的 60 个日收益；样本不足、非有限、非正或接近零波动率均 fail-closed。
- 仅在完整 cluster 的共同候选集合上排名；不改变 filled slot allocation。
- 没有可验证冻结 U1、R39 paired control、三折回测和因果行情输入时，所有实验指标写 `unavailable`，不得伪造晋级结论。

## 必须验证

- momentum/volatility_60 的精确计算、非有限值、零波动率和样本不足。
- 未来行情不得进入 volatility 窗口，signal-date close 口径明确。
- cluster 缺失成员不得用部分成员平均形成有效排名；完整 cluster 的排名基数稳定。
- R39 生命周期和诊断字段保持，R74 不包含 inverse-volatility weighting。
- focused、R39/R40/Runner/catalog 回归、Batch 3C 幂等产物及独立 Luna P0/P1 review。
