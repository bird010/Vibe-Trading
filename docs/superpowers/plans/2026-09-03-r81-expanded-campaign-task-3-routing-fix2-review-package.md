# Task 3 R87 路由修复第二次新鲜复审

## 结论

**PASS**。本次未发现 P0/P1 问题。R86/R87 的批服务路由和配套 runner 路由均只新增了两个明确的角色池前缀；runner 测试路径正确；R87 前次两个 P1 修复仍然有效。没有发现本次路由修复改变既有策略、PIT 优先级或 execution contract 语义的证据。

本次没有修改实现文件，没有运行长回测。

## 审查范围

- 当前工作树与 `git diff --check`。
- 前次 Task 3 初审、P1 修复复审和路由修复复审包及 JSON。
- `agent/src/stockpred/fund_rotation/batch_service.py` 及其测试。
- `agent/scripts/run_r81_combination_batch.py` 及其测试。
- R87 实现、注册表和 R87 专项测试，用于复核前次 P1 修复。

## 路由核对

### 批服务路由

`BatchService._resolve_execution_rule_context()` 的工作树差异仅向既有 `role_strategy_prefixes` 元组追加：

- `ai_rotation_r86_r81_transition_cap_50`
- `ai_rotation_r87_r81_role_rank_buffer`

`use_role_pool` 仍通过 `str(strategy_id).startswith(role_strategy_prefixes)` 判断；命中后仍读取 `snapshot.role_universe_codes`，未命中则仍读取 `snapshot.universe_codes`。既有真实 PIT loader 优先级、`RESEARCH_ONLY` 限制和静态规则构造调用未改变。

### runner 路由

`run_r81_combination_batch.py` 的 `_execution_rule_loader()` 使用同一组显式前缀，工作树差异没有加入模糊匹配或其他 R86/R87 变体。`main()` 将该 loader 传入 `BatchService`，并继续使用同一 snapshot、数据帧 loader 和 `RESEARCH_ONLY` 请求路径。

测试中的 `_RUNNER_PATH` 由 `agent/tests/fund_rotation/test_r81_runner_output_root.py` 的 `Path(__file__).resolve().parents[2] / "scripts" / "run_r81_combination_batch.py"` 解析到 `agent/scripts/run_r81_combination_batch.py`；实际导入并调用 `_execution_rule_loader()` 成功。

## R87 前次 P1 修复

- 全局状态隔离：R87 通过 session 实例的 `_rank_roles()` seam 实现排名缓冲，不替换经济角色模块的全局 `rank_scores`。专项测试验证全局函数身份保持不变。
- artifact 一致性：R87 在父层 decision 返回后追加 `role_rank_buffer`，同步写回当前 `_decision_log`，再返回替换后的 decision；专项测试验证 returned decision 与 finalized `decisions` artifact 的 diagnostics 相等。

## 既有语义不变性

- 共享经济角色策略的差异只增加实例级 `_rank_roles()` seam，默认实现仍调用原 canonical `rank_scores`，属于为 R87 隔离状态而做的最小扩展。
- 路由差异未触及 PIT/data snapshot 选择、真实 PIT loader 优先级、execution contract、成交执行或费用规则。
- 注册表差异仅注册 R87；未改动 R81、R63 或既有策略的实现。

## 验证结果

本次安全运行的聚焦测试：

- `test_batch_service.py` 的 R86/R87 角色池路由用例：**1 passed**。
- `test_r81_runner_output_root.py`：**2 passed**。
- `test_ai_rotation_r87_r81_role_rank_buffer.py`：**7 passed**。
- `test_strategy_catalog.py`：**23 passed**。

上述 pytest 命令均出现既有 `.pytest_cache` ACL warning，不影响测试断言。

前次复审已记录：带 `tmp_path` 的 PIT 优先级测试受 Windows 临时目录 ACL 阻塞，不能据此宣称该测试完整通过。本次通过当前 diff 静态确认该 PIT 优先级分支未改变。

## 发现

### P0

无。

### P1

无。

### P2

1. 路由回归测试当前把 R86 和 R87 放在同一个请求中，尚未分别覆盖 R86-only 与 R87-only 请求；精确前缀的静态核对和当前混合请求测试已证明现有路径，但拆分用例会提供更直接的回归保护。
2. Windows pytest 临时目录 ACL 仍阻止带 `tmp_path` 的 PIT 优先级测试完整启动或清理；这是环境限制，不是本次路由差异导致的代码失败。

### P3

1. pytest cache 写入仍有既有 `PytestCacheWarning`。

## 放行判断

本次路由修复第二次新鲜复审为 **PASS**。R86/R87 可继续进入 brief 规定的配对回测；本复审不替代回测后的 Champion gate，也不授权部署。
