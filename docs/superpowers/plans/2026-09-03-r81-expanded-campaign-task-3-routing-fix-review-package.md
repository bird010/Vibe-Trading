# Task 3 R87 路由修复新鲜复审

## 结论

**PASS**。未发现 P0/P1 问题。R86/R87 的研究静态规则路由修复足以覆盖仅包含 R86 或 R87 的批次；未改变真实 PIT 优先级、执行规则语义、回测执行语义或既有策略行为。R87 上一轮两个 P1 修复仍然成立。

## 审查范围

- 读取 Task 3 初审与 P1 修复复审包及 JSON。
- 检查当前 R87 文件、R86 父层、注册表，以及 `agent/src/stockpred/fund_rotation/batch_service.py` 和 `agent/tests/fund_rotation/test_batch_service.py` 的当前差异。
- 本次未修改实现文件，未运行长回测。

## 路由修复验证

`BatchService._resolve_execution_rule_context()` 的当前差异只向既有 `role_strategy_prefixes` 元组追加：

- `ai_rotation_r86_r81_transition_cap_50`
- `ai_rotation_r87_r81_role_rank_buffer`

`use_role_pool` 仍只由这些前缀匹配结果决定；若命中，则继续读取 `snapshot.role_universe_codes`，否则仍使用原 `snapshot.universe_codes`。真实 `execution_rule_context_loader` 的优先级和非 `RESEARCH_ONLY` 拒绝逻辑未变。因此 R86-only、R87-only 和 R86/R87 混合请求都会进入角色池，而普通既有策略不会因本修复改变路由。

新增回归测试构造了普通池 `E1` 与角色池额外代码 `513100.SH`，R86/R87 请求返回两者，证明角色池路径被选中。源码差异核对确认没有新增其他前缀，也没有修改 PIT/data snapshot 或 execution contract。

## R87 前次 P1 复核

### 进程级全局状态隔离

R87 当前通过 `EconomicRoleR81TransitionCap50Session._rank_roles()` 的实例 seam 实现角色排名缓冲，不再替换经济角色模块的全局 `rank_scores`。此前的身份保持测试仍通过，因而不存在跨 session 的 monkeypatch 串扰。

### 诊断与 decisions artifact 一致

R87 在父层 evaluate 返回后追加 `role_rank_buffer`，并立即更新当前 `_decision_log` 行后再返回替换后的 decision。现有集成测试仍断言 finalized `decisions` 行的 diagnostics 与 returned decision 完全相等。

## 测试证据

- R86/R87、经济角色回归：**29 passed**。
- R87 专项：**7 passed**。
- 策略目录：**23 passed**。
- R86/R87 角色池路由：**1 passed**。
- 所有 pytest 命令均出现既有 `.pytest_cache` ACL warning。
- 含 `tmp_path` 的 PIT 优先级测试在系统默认临时目录创建阶段因 Windows ACL 报 `PermissionError`；尝试改用外部临时目录时测试阶段可启动，但 session cleanup 同样被 ACL 阻断。因此 PIT 不变性以当前 diff 的 loader 优先级未改动为静态证据，并明确不宣称该条 pytest 已通过。

## 发现

### P0

无。

### P1

无。

### P2

1. 路由回归测试把 R86 与 R87 放在同一个请求中，未分别以 R86-only、R87-only 两个独立请求断言；实现中的两个精确前缀已完成静态核对，当前不阻塞回测，但建议后续补充两个单策略用例。
2. 默认 Windows pytest 临时目录 ACL 仍使带 `tmp_path` 的 PIT 回归无法完整收尾；这是环境限制，不是本次路由差异引起的功能失败。

### P3

1. `.pytest_cache` 写入 warning 为既有工作区权限问题。

## 放行判断

本轮路由修复复审为 **PASS**。R86/R87 可以继续进入 brief 规定的配对回测；本审查不替代回测后的 Champion gate，也不授权部署。
