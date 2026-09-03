# Task 5 R90 独立审查报告

## 结论

**PASS**。未发现 P0/P1 问题；R90 可以进入 brief 规定的唯一一次 paired backtest（R88 Champion vs R90 Challenger）。本审查未运行长回测，也未修改实现代码。

保留两项 P2：

1. 现有测试验证了 R90 的路由前缀存在，但没有把 R90-only 路由断言作为独立的持久化测试用例；本次以同等输入执行 runner loader 做了现场核验。
2. 没有针对 R90 session 的真实 `data_view` 做未来日期扰动测试；但 R90 复用 R88 的 `_causal` 与 `compute_adjusted_return_126d`，并在调用处再次按 `signal_date` 截断。

## 审查范围

- brief：`docs/superpowers/plans/2026-09-03-r81-expanded-campaign-task-5-r90-brief.md`
- 实现：`agent/backtest/fund_rotation/strategies/ai_rotation_r90_r81_role_r61_dual_horizon/`
- 测试：`agent/tests/fund_rotation/test_ai_rotation_r90_r81_role_r61_dual_horizon.py`
- 注册：`agent/backtest/fund_rotation/strategies/registry.py`
- 路由：`agent/src/stockpred/fund_rotation/batch_service.py`、`agent/scripts/run_r81_combination_batch.py`

## 核验结果

### 1. 50/50 标准化短中期角色分数

`fuse_dual_horizon_role_scores` 仅收集同时具有有限 short score 与 medium return 的角色；在完整候选集合上分别计算 population z-score，并以 `0.5 * short_z + 0.5 * medium_z` 排名。并列时使用 `(fused_score 降序, role_id 升序)`，没有 cluster/member 状态参与。

这与 brief 的“现有 R81 角色短期分数 + 当前代表的因果 126D adjusted return，50/50 标准化融合”一致。缺失、非数值、非有限或不可用的任一分量均不进入融合排名，满足 fail-closed。

### 2. 因果性与 127 个有效观测门槛

R90 在 `_rank_roles` 中通过 `view.daily_bars(..., lookback=127)` 和 `view.fund_adjustments(lookback=127)` 获取数据，随后调用 `_causal(..., signal_date)`；中期收益复用 R88 的 `apply_role_medium_trend_gate`，最终复用 `compute_adjusted_return_126d`。

该计算要求调整后收盘价恰好具有 127 条观测、全部非空且为正，并使用 signal-date adjustment factor；不足、缺失覆盖、无效价格或非有限收益均返回不可用状态。R88 的 `adjusted_return_126d > 0` gate 仍单独作用于 `valid_roles`，因此 R90 的融合分数不会绕过 gate。

### 3. 上游机制保持

R90 session 继承 `EconomicRoleR81RoleR60GateSession`，并且排序阶段显式复用 R86 的基础角色排序，再应用 R88 gate 和 R87 rank buffer。实现及 pipeline assertions 均确认：

- R81 动态 representative、角色生命周期与防御语义保留；
- R86 一周正向目标暴露 50% 上限保留在 evaluate/post-decision 阶段；
- R87 Top3 入场、Top4 退出 hysteresis 保留；
- R88 当前代表 causal 126D 正趋势 gate 保留；
- R90 唯一新增机制是 50/50 short/medium role ranking score。

没有修改 R61、R81、R86、R87、R88、PIT/data contract 或 execution semantics。

### 4. 诊断与 artifacts

R90 在返回 decision 的 `diagnostics["dual_horizon_role_score"]` 中记录规则、两个分量、127 观测要求及逐角色融合详情，并同步更新 `_decision_log[-1]` 的 `decision_id` 与 `diagnostics`。因此最终 `decisions` artifact 与返回的 decision diagnostics 一致；R86 的 capped target/cash 与 decision trace patch 仍由上游执行。

### 5. 注册与 role-only 路由

R90 已加入 registry 默认策略及 catalog assertions；`batch_service.py` 与公共 batch runner 的 role strategy prefix 均包含 `ai_rotation_r90_r81_role_r61_dual_horizon`。

本次现场构造 R90-only `RESEARCH_ONLY` request，使用普通 universe `("E1",)` 与角色 universe `("E1", "513100.SH")` 调用 runner loader，实际得到 instruments `['513100.SH', 'E1']`，确认 R90-only 请求进入 role universe。没有观察到普通 universe 回退。

## 测试证据

执行命令：

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_ai_rotation_r90_r81_role_r61_dual_horizon.py agent/tests/fund_rotation/test_ai_rotation_r86_r81_transition_cap_50.py agent/tests/fund_rotation/test_ai_rotation_r87_r81_role_rank_buffer.py agent/tests/fund_rotation/test_ai_rotation_r88_r81_role_r60_gate.py agent/tests/fund_rotation/test_strategy_catalog.py agent/tests/fund_rotation/test_r81_runner_output_root.py -q
```

结果：`43 passed, 1 warning in 8.70s`。warning 是既有 pytest cache ACL warning。另执行 `git diff --check`，无 whitespace error；现场 R90-only routing loader 核验通过。

## 后续门槛

本审查只批准进入一次 paired backtest，不替代 backtest 结果或 Champion gate。后续必须使用固定 interval、snapshot、fold manifest 与 execution contract，以 R88 为 Champion、R90 为 Challenger；只有满足 Val Sharpe 严格更高、Val annual return 不低、MDD 恶化不超过 1pp、跨 folds Sharpe 胜出超过半数且 PIT/causal/execution/comparability gates 全部通过，才可晋级。
