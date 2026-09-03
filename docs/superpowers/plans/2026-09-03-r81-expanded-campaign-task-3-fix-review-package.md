# Task 3 R87 P1 修复复审

## 结论

**PASS**。上一轮两个 P1 均已修复；未发现新的 P0/P1 问题。R87 可以进入 brief 要求的唯一一次 R86 vs R87 配对回测，但本次复审本身未运行长回测。

## 复审范围与证据

- 已读取 Task 3 brief、上一轮审查 JSON，以及当前 R87 实现、R86 父层、注册表和测试。
- 未修改任何实现文件。
- 聚焦测试、R86 回归、策略目录和经济角色回归：**52 passed**，1 个既有 `PytestCacheWarning`（工作区 pytest cache ACL，非功能问题）。
- `git diff --check` 通过。

## P1 修复验证

### 1. 进程级 monkeypatch 已完全移除

当前 R87 不再导入或替换经济角色模块的全局 `rank_scores`。父类新增 `_rank_roles()` 实例 seam，默认实现仍调用原 canonical `rank_scores`；R87 仅覆写自身 session 的 `_rank_roles()`，先取得父类的角色排序，再在会话实例的 `_previous_selected_roles` 上应用 Top3 入场/Top4 退出缓冲。没有对模块符号、全局函数或其他 session 做运行时修改。

现有测试 `test_r87_role_ranking_does_not_replace_process_global_rank_scores` 直接验证原模块函数身份不变；测试套件也覆盖 R86/R81 经济角色回归。因此上一轮“全局状态串扰”P1 已关闭。

### 2. returned diagnostics 与 decisions artifact 已同步

R87 在 `super().evaluate()` 返回后复制并追加 `role_rank_buffer` diagnostics，然后将最终 diagnostics 写入 `self._decision_log[-1]`，最后返回替换后的 decision。复审测试 `test_r87_returned_and_finalized_decision_diagnostics_are_identical` 调用真实 R87 override 的 artifact 更新路径，并断言 `finalize()` 输出的 decisions 行与 returned decision 的 diagnostics 完全相等。因此上一轮 artifact divergence P1 已关闭。

## 语义与边界检查

- R87 仍继承 `EconomicRoleR81RoleRankBufferSession -> EconomicRoleR81TransitionCap50Session`，保留 R86 的 50% 单周正向暴露上限；R86 再保留修复后的 R81 动态代表、防御资格和生命周期路径。
- R87 的缓冲只作用于 `ROLE_IDS` 的当前 role scores 和 session-local selected-role 状态，不引入 R63 的 cluster ID 或 cluster state。
- canonical ranking 只返回 eligible score；R87 对无效/缺失分数不会保留，且保持确定性的 role-ID tie break。
- 注册表中 R87 ID 唯一，catalog 测试通过。
- 未修改 R63、R81、R86、公共 runner、PIT/data contract 或 execution 语义；本次复审未运行长 paired backtest。

## 保留观察项（P2/P3，不阻塞）

- 尚缺少通过真实连续 `evaluate()` 调用覆盖状态连续性、refresh epoch reset，以及 transition cap uncapped/no-op 的测试。
- diagnostics 尚未单独暴露最终 selected role 列表或独立 role-buffer artifact；当前 `role_rank_buffer` 已写入 decisions，且与 returned decision 一致。
- pytest cache ACL warning 为既有环境问题。

## 放行判断

实现审查放行。按 brief，下一步只能运行一次固定区间 `20130329..20220729` 的 R86 vs R87 配对批次，并依据五折 evidence 和 Champion gate 判定，不得仅凭本次代码测试替换 Champion。
