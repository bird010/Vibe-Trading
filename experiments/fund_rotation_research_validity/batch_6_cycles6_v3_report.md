# Batch 6：幸存机制组合

- 组合策略：`ai_rotation_r78_survivor_combo`
- 组合规则：只有单变量 `promotion_allowed=true` 且 review P0/P1=0 的冻结机制才可进入
- 当前 survivor：无；组合状态 fail-closed
- manifest SHA-256：`a679af0ed3170ed0fb36b6fdd5c9ed7d492135312b8efc014c72aebb94857975`
- 实验状态：`UNAVAILABLE_INPUTS`
- promotion_allowed：`false`

## 证据边界

Batch 3A/3B/3C 与 Batch 5 的单变量产物均未提供 promotion_allowed=true 的实际前瞻证据，因此不组合任何机制，不声称 winner，不把代码审查通过当作收益晋级。组合后的三折、normal/2x/T+1/T+2/neighbor 和逐层边际贡献全部保持 unavailable。

Shadow 26 周 + 6 次硬门槛仍为 `UNRESOLVED`；104 周是建议观察长度。Shadow 是持续 append-only 过程，不能标记为完成资格。

## 停止方向

在获得真实单变量晋级证据前，停止 Batch 6 收益组合、参数邻域比较和 winner 选择；不扩大 Rxx 网格，不引入新可调参数。

## 最小改动自评

仅新增 R78 evidence-gated composition adapter、显式 registry 条目、focused tests、Batch 6 登记脚本、本中文报告和 acceptance matrix；未修改既有策略算法、Runner、execution ledger 或 Shadow 账本。
