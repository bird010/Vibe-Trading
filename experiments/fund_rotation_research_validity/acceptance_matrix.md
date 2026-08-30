# 研究有效性验收矩阵

| 要求 | 当前证据 | 状态 | 说明 |
|---|---|---|---|
| Batch 0 v2 summary 合同 | Batch 0 report/repair manifest | 已完成边界修复 | v2 与 legacy 分离，legacy 不参与新排名 |
| Batch 0 原始订单/持仓/净值不变 | Batch 0 consistency tests | 已验证 | 只改 summary/report 产物 |
| PIT U0/U1 与 identity 去重 | Batch 1 manifest、Batch 4/5 adapter 测试 | 已实现 | 新组合仍要求固定 U1，不按原始基金重复计数 |
| R71 容量、回退与 blocked attempts | Batch 2 manifest、R71 tests | 已实现边界 | 容量不足回退/现金和 lot-size 规则有测试 |
| R39/R40 执行与成本语义 | R39/R40 既有回归、Shadow A 账本 | 已保留 | 本计划未修改公共 Runner 或 execution ledger |
| Batch 3A 绝对动量单变量 | Batch 3A manifest/report | 未晋级 | 只增加 R126d 门；输入/前瞻证据不可用 |
| Batch 3B 多周期排名单变量 | Batch 3B manifest/report | 未晋级 | 只替换排名；输入/前瞻证据不可用 |
| Batch 3C 波动率调整单变量 | Batch 3C manifest/report | 未晋级 | 只替换排名；输入/前瞻证据不可用 |
| 单变量趋势/风险实验总门 | Batch 3A/3B/3C/5 manifest | 未晋级 | 产物均明确 `promotion_allowed=false` 或输入不可用 |
| Batch 4 机制消融 | Batch 4 manifest、Luna gate | 已完成边界验证 | U1/paired 输入缺失，不能解释三臂胜负 |
| Batch 6 仅组合幸存机制 | R78 adapter 与 `batch_6_cycles6_v3/manifest.json` | 被阻断 | 当前没有任何 `promotion_allowed=true` survivor |
| 三折 paired backtest | Batch 6 manifest | 未完成 | `0/3`，不得用单元测试替代 |
| 正常成本与 2× 成本 | Batch 6 manifest | 未完成 | 全部 unavailable |
| T+1/T+2 延迟 | Batch 6 manifest | 未完成 | 全部 unavailable |
| 参数邻域 | Batch 6 manifest | 未完成 | 未进行事后参数搜索 |
| fold contribution、switch、持有期、阻塞率、现金占比 | Batch 6 manifest | 未完成 | 全部 unavailable |
| 组合后逐层边际贡献 | Batch 6 manifest | 未完成 | 无 survivor，组合未执行 |
| 多重检验记录 | 研究设计文档与 Batch 6 manifest | 已记录停止边界 | 当前因无晋级 survivor 未执行组合检验；不以统计修正替代前瞻验证 |
| Shadow 冻结配置/版本/哈希 | `shadow_a_cycles6_v2/frozen_strategy_manifest.json` | 已冻结 | R40 50% cap，不调整为其他上限；旧 `shadow_a`/`shadow_a_cycles6` 作为历史证据保留 |
| Shadow 事件账本与双净值 | `shadow_a_cycles6_v2/shadow_manifest.json` 与账本产物 | 未完成 | 需未来价格后写入 execution/ledger 事件 |
| Shadow 26 周和 6 次完整调仓门槛 | Shadow qualification artifacts | 未完成 | 当前未满足资格门槛 |
| Shadow 104 周建议观察 | `shadow_a_cycles6_v2/shadow_manifest.json` | 未解决 | 26 周 + 6 次是硬门槛；104 周为建议观察长度，当前仍是 active append-only 过程 |
| 结果晋级/生产切换 | Batch 6 manifest | 禁止 | 无 winner、无 promotion、无生产切换 |
| 每步独立 Luna high review | 各 Task report 与 review ledger | 已通过 | Task 10 final whole-branch reviewer Jason：P0/P1/P2/P3=`0/0/1/0`；P2 已记录，P0/P1 已清零 |

## 最小改动与停止边界

Batch 6 只增加 evidence-gated 组合适配器和登记证据，不改既有策略实现、公共 Runner、execution ledger 或 Shadow 账本；在单变量晋级证据出现前停止组合收益解释和参数扩展。
