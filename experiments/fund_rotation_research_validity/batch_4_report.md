# Batch 4：Momentum/Cluster/Carry 三臂消融

- M0：Momentum only；M1：Momentum + Cluster；M2：Momentum + Cluster + R39 carry
- 三臂共同身份去重：`true`
- manifest SHA-256：`29d2768fa04cb05ad29e8c5f5ce12c49d05d7be3a1ffb51ebf0bf71f2c9eeb85`
- 实验状态：`UNAVAILABLE_INPUTS`
- promotion_allowed：`false`

## 证据边界

三臂只改变声明的 mechanism toggle；数据快照、日历、执行合约、成本和延迟应完全共享。M0 关闭 cluster 选择但不关闭 PIT identity de-dup；M1 加入 cluster/representative 选择；M2 只在 M1 基础上使用既有 R39 staging 与 incumbent carry。没有引入 direct-correlation 迁移。

当前没有可验证 U1、三臂 paired backtest 或三折输入，故 duplicate underlying exposure、cluster/carry marginal contribution、持有期、switch、换手、阻塞率、收益风险指标、fold contribution 和 normal/2x/T+1/T+2 全部为 `unavailable`；不把单元测试结果当作收益证据，不作架构晋级结论。

## 最小改动自评

仅新增固定消融适配器、focused tests、Batch 4 登记脚本和中文报告；未修改 R39/R40、公共 Runner、execution ledger、平台架构或 direct-correlation 实现。
