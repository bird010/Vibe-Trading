# Task 10 完成报告：Batch 6 幸存机制组合与最终验收矩阵

## 实施范围

新增 R78 evidence-gated 组合适配器：只有单变量产物 `promotion_allowed=true` 且独立审查 P0/P1 均为零的机制才可组合；组合顺序固定为 ranking → risk → defense，不引入新可调参数，不允许重复机制，并传播每层 source hash。

当前 Batch 3A/3B/3C/5 产物均没有晋级证据，因此 survivor 选择器返回空，Batch 6 诚实 fail-closed；没有把 review PASS 当作收益 winner，也没有把单元测试当作前瞻收益证据。

新增中文 `acceptance_matrix.md`，逐项记录 PIT、执行、三折、成本、延迟、参数邻域、边际贡献和 Shadow 26 周 + 6 次硬门槛；104 周仅为建议观察长度。Shadow 仍明确为 `UNRESOLVED` 的 active append-only 过程，不能标记为完成资格。

## 验证结果

Focused composition/catalog/API：`36 passed`；最终受影响范围回归：`148 passed, 3 warnings`（既有 Pydantic protected-namespace warnings）。
完整 `fund_rotation` 目录已执行；首次收集被 4 个既有 engine/review 模块缺少 `defusedxml` 阻断。独立复核的非临时可用子集为 `1452 passed, 2 failed, 3 skipped`，另有 Windows ACL 导致的临时目录错误；失败位于未修改的历史 fixture/验证路径，未将其伪装成全量通过；Task 10 新增及受影响范围均无失败。
Batch 6 登记脚本在版本化输出目录连续执行两次均 exit 0，脚本 source hash 一致，survivor 数为 4 但全部 `promotion_allowed=false`，因此组合状态保持不可用。当前正式版本采用 26 周 + 6 次硬门槛，104 周为建议观察长度；旧 `cycles6`/`v2` 产物作为未采用历史版本保留：

- manifest：`A679AF0ED3170ED0FB36B6FDD5C9ED7D492135312B8EFC014C72AEBB94857975`
- report：`23B5B4F765E617939AC3D26FE1F80BCDFCCE975ECF169DC990A05EEE3FAE8453`
- Shadow manifest：`D3A68FB1556A07607847C15B65435D482D7B6C16BEF5ABE152DF72DA10072F1F`
- Shadow report：`CBF6C235B1C0ACD299A8D1C99532599E8DA54F512A039D5FEA3E34533F0AE65D`
- Shadow integrity：`629D712C3F33237C8931637AB3EA9E7D04B3B7EFE74D9216A45CFD4A25316D53`

manifest 状态为 `UNAVAILABLE_INPUTS`，`promotion_allowed=false`，fold 为 `0/3`，normal/2x/T+1/T+2/neighbor 和逐层边际贡献全部 `unavailable`；Shadow 26 周 + 6 次硬门槛为 `UNRESOLVED`，104 周为建议观察长度。

## 最小改动自评

仅新增 R78 组合适配器、一个 registry 条目、必要测试、Batch 6 登记脚本、中文报告和验收矩阵；未修改既有策略算法、公共 Runner、execution ledger 或 Shadow 账本。

## 独立审查门

Task 10 实现和验证已完成。最终独立 `gpt-5.6-luna/high` whole-branch reviewer（Jason）通过：P0/P1/P2/P3=`0/0/1/0`；P2 仅为此前测试结果记录滞后，已在本报告补记。只能声明研究实现与证据边界完成，不得声明策略晋级或 Shadow 26 周 + 6 次硬资格完成；104 周仅为建议观察长度。
