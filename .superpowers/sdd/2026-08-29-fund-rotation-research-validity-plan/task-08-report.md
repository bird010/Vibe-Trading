# Task 8 完成报告：Batch 4 Momentum/Cluster/Carry 消融

## 实施范围

新增固定三臂消融适配器：M0=Momentum only，M1=Momentum+Cluster，M2=Momentum+Cluster+R39 carry。
三臂都强制保留 identity de-dup；M0 只关闭 cluster 选择，M1 加入已有 representative/cluster 选择，
M2 只在 M1 基础上复用既有 R39 staging 与 incumbent carry。三臂的 data snapshot、calendar、execution、cost、delay 合同由 Batch 4 manifest 统一绑定。

实现文件：

- `agent/backtest/fund_rotation/ablation.py`
- `agent/tests/fund_rotation/test_ablation.py`
- `experiments/fund_rotation_research_validity/batch_4_ablation.py`
- `experiments/fund_rotation_research_validity/batch_4/manifest.json`
- `experiments/fund_rotation_research_validity/batch_4_report.md`

按计划，当前 runner 不要求将研究消融臂注册为正式策略，因此未修改 registry；也未修改 R39/R40、公共 Runner、execution ledger 或 direct-correlation 架构。

## 验证结果

Focused 与受影响回归：`114 passed`；Batch 4 focused 测试包含固定 U1 代表、完整 U1 簇成员 canonical tie-break、脚本哈希绑定和 fail-closed 稳定性。

Batch 4 登记脚本连续执行两次，均为 exit 0；不可变产物哈希为：

- manifest：`29D2768FA04CB05AD29E8C5F5CE12C49D05D7BE3A1FFB51EBF0BF71F2C9EEB85`
- report：`BA8E23AA41168341027A10FD8762D7A1E6AE419E9229C72B1AB71770594DA6DD`

manifest 状态为 `UNAVAILABLE_INPUTS`，`promotion_allowed=false`，三折为 `0/3`。U1、三臂相同快照/执行输入及 paired backtest 均不可用，因此 duplicate underlying exposure、cluster/carry marginal contribution、持有期、switch、换手、阻塞率、收益风险指标和 fold contribution 全部为 `unavailable`；没有把单元测试当作收益证据，也没有作架构晋级结论。

## 最小改动自评

只新增固定消融适配器、focused tests、Batch 4 登记脚本和中文报告；没有引入 direct-correlation 迁移、可调参数网格或平台级重构。

## 独立审查门

首轮独立审查发现的两个 P1 已分别修复：identity 去重先固定 U1 字典序最小代表，再按 momentum 排序；cluster 平分使用完整 U1 簇成员的最小 code，即使该成员没有有效 momentum 分数。最终独立 `gpt-5.6-luna/high` reviewer（Boyle）通过：P0/P1/P2/P3=`0/0/0/0`；受影响回归 `91 passed`，Batch 4 连续运行结果相等且保持不可晋级。Task 8 gate 已关闭，可以进入 Task 9。若 U1/paired 输入继续缺失，仍只记录不可用证据，不得声称 M0/M1/M2 胜负。
