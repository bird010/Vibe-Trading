# R100 / R29 实现报告

## 结果

已实现独立策略 ID `ai_rotation_r100_r81_r88_invvol_slots`。fresh review 发现并确认 R86 cap、非等权守恒和防御同码隔离问题，已按最小变更修复。

逆波动候选现在使用 base-weight 加权因子均值，保持已调槽位总质量；随后复用 R86 的 `apply_transition_cap(..., 0.50)`，确保最终目标仍符合 R86 的正向新增暴露上限。与 `defense_asset` 同码的代表从可调槽位中保护，非槽位代码不参与调整。

## 改动文件

- `agent/backtest/fund_rotation/strategies/ai_rotation_r86_r81_transition_cap_50/r100_r81_r88_invvol_slots.py`：R100 策略、角色层权重函数、diagnostics。
- `agent/tests/fund_rotation/test_ai_rotation_r100_r81_r88_invvol_slots.py`：权重、现金、回退、独立策略管线测试。
- `agent/backtest/fund_rotation/strategies/registry.py`：注册 R100。
- `agent/src/stockpred/fund_rotation/batch_service.py`、`agent/scripts/run_r81_combination_batch.py`：角色 universe 路由。
- `agent/tests/fund_rotation/test_strategy_catalog.py`、`agent/tests/fund_rotation/test_r81_runner_output_root.py`：注册和路由回归断言。
- `docs/superpowers/plans/2026-09-03-r100-fresh-review-report.md`：P1 审查依据。

由于新目录创建被当前工作区 ACL 拒绝，R100 模块放在已有 R86 目录中；策略 ID、descriptor、registry entry 和行为均独立。

## TDD 与验证

- RED：新测试先因 R100 模块不存在而失败，`3 failed`。
- RED：新增 P1 修复测试在修复前因缺少 cap wrapper/保护参数而失败，`6 failed`。
- GREEN/focused 与 R81–R91 相关回归：最终合并运行 `92 passed`。
- `git diff --check` 未能干净通过：既有 `test_strategy_catalog.py` 的 CRLF/工作区权限导致 trailing-whitespace 报告；不是 R100 逻辑错误。
- `py_compile` 受已有 `__pycache__` ACL 拒绝，未产生新的编译结论；pytest 已成功导入并执行 R100。

## 语义边界与风险

- 未改变 R88 的角色评分、代表选择/锁定、Top3/Top4 缓冲、126 日趋势 gate、生命周期、R86 迁移上限、防御和执行合同。
- 质量状态非 `VALID`、窗口不足、代表列缺失、NaN/Inf 或波动计算异常时整体回退传入的 R88 槽位权重。
- 当前实现使用 R88 已完成上游后的目标权重作为槽位基数，因此不会重新构造生命周期或防御层；diagnostics 记录窗口、sigma、因子和回退原因。
- fresh review 在修复后通过；随后已完成 R88 vs R100 paired backtest，结果记录在 campaign ledger。
- 后续保护边界修复：helper 保留 protected code 原始 base 权重，`merge_adjusted_role_weights` 仅写回 adjustable codes；混合 protected/adjustable/non-slot 守恒测试已加入并通过。
- 第二次 fresh review 修复：移除 R100 对完整组合 shared cap 的调用，改用 `apply_r100_adjustable_transition_cap`；非 adjustable 正增量计入不可控暴露，adjustable 仅使用剩余预算，且不偷偷缩放防御/非槽位目标。新增 cap/守恒测试通过。
- 第三次 fresh review 发现 descriptor 名称仍为 R88，已改为 R100；改名后目录/路由验证仍通过。
