# Task 8 实施简报：Batch 4 Momentum/Cluster/Carry 消融

## 目标

严格执行文档工作六：固定 U1 上比较三个机制臂：M0 仅 Momentum，M1 Momentum+Cluster，M2
Momentum+Cluster+R39 carry。三臂共享同一数据快照、日历、执行合约、成本和延迟。

## 最小改动约束

- 新增最小消融适配器和 focused tests；只有当前 runner 确实需要时才注册显式 adapter。
- 三臂都保留 PIT 资产身份去重；M0 关闭的是 cluster 选择机制，不是 identity de-dup。
- 不修改 R39/R40、Runner、execution ledger 或实现 direct-correlation 迁移。
- 无可验证冻结 U1、三折 paired 输入时，Batch 4 只登记证据边界，所有比较指标为 `unavailable`，不得声称聚类有无价值。

## 必须验证

- 三个固定 arm 的机制开关准确：M0=(Momentum, no Cluster, no Carry)，M1=(Momentum, Cluster, no Carry)，M2=(Momentum, Cluster, R39 Carry)。
- 数据快照、日历、执行、成本、延迟绑定一致；身份去重开关不得被任何 arm 关闭。
- carry 只能使用 R39 的 `apply_incumbent_carry` 语义；不引入新参数。
- 记录重复底层资产暴露、carry marginal contribution、cluster marginal contribution、fold contribution；输入缺失时明确 `unavailable`。
