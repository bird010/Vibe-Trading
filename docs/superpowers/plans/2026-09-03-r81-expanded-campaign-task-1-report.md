# Task 1 实现报告：R81 固定防御资产资格处理

## 状态

实现与聚焦回归已完成并已提交。R81 修复后的全量 anchor/child 回测未完成：运行目录写入受到 Windows ACL 拒绝，后续耗时进程已按用户要求停止。因此本任务不宣称 anchor 已发布或可比较。

## 改动文件

- `agent/backtest/fund_rotation/strategies/economic_role_rotation/strategy.py`
  - 仅在 R81 descriptor 下检查现有 `signal_eligible`。
  - `511010.SH` 不在信号日 eligible pool 时传入 `None`，复用既有 `apply_defense_asset()` 现金回退。
  - 记录 `FIXED_SHORT_BOND_UNAVAILABLE`，并将 diagnostics 的 `defense_asset` 置为 `None`。
  - R79/R80 继续使用原有无条件固定短债路径。
- `agent/tests/fund_rotation/test_r81_fixed_defense_eligibility.py`
  - 增加不可用回退现金测试。
  - 增加可用时固定短债行为保持测试。
- `agent/runs/fund_rotation/experiments/fund_rotation_r81_combinations_20260903_v2/ledger.jsonl`
  - 追加 Task 1 修复哈希、快照、折叠清单、区间和 anchor 运行阻塞偏差记录。

## TDD 与测试证据

先写测试并运行到预期失败：

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_r81_fixed_defense_eligibility.py -q
2 failed
```

失败表现为旧实现无条件生成 `511010.SH`；不可用场景目标权重包含 `511010.SH`，可用场景也暴露出既有 staged-reentry 基线为 0.75。

修复后聚焦命令：

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_r81_fixed_defense_eligibility.py agent/tests/fund_rotation/test_economic_role_rotation.py -q
20 passed, 1 warning in 0.61s
```

另已运行角色组合回归：

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_economic_role_rotation.py agent/tests/fund_rotation/test_ai_rotation_r82_economic_role_dynamic_rep_r57_signal.py agent/tests/fund_rotation/test_ai_rotation_r83_r81_r57_r77_combo.py agent/tests/fund_rotation/test_ai_rotation_r84_r81_r57_r62_combo.py agent/tests/fund_rotation/test_ai_rotation_r85_r81_r74_combo.py -q
34 passed, 1 warning in 2.83s
```

warning 是既有 `.pytest_cache` 写入权限 warning，不是测试失败。

## R81 语义保持情况

- 经济角色分类、动态代表选择、代表锁定、硬失败重选、评分、排序、staged re-entry、incumbent carry 和执行语义未改动。
- 可用时 `511010.SH` 仍承接既有可用现金；测试观察到目标权重为 0.75、现金为 0.0，与修复前基线一致。
- 不可用时不强行写入目标权重，现金保留，并产生明确 unavailable reason。
- 公共 Runner、PIT/data contract、公共风险层和 `run_r81_combination_batch.py` 未修改。

## Anchor / ledger / 残余风险

- 固定区间：`20130329..20220729`。
- 当前实验快照：`7596807626fdf7f1aa9bdaddd84cd4575e15ac473c8331879d841ecacd941de6`。
- fold manifest：`agent/runs/fund_rotation/experiments/fund_rotation_r81_combinations_20260903_v2/fold_manifest.json`，5 folds。
- 修复实现 hash（git blob）：`2ce5ddbdcfd9b039e0fed3091e520c2fa9339d66`。
- 新 anchor 尝试在提交 batch 前因 Windows ACL 拒绝写入 `agent/runs/fund_rotation/strategy_batches/idempotency/...`；补齐 `PYTHONPATH` 后仍为环境写权限问题，未产生新的 batch/run ID。旧失败 run 不冒充修复后结果。
- 当时尚未验证两个 child 的终态、contract violation、publishable/comparable anchor；该残余风险已由下方 P1 fix round 1 的成功批次关闭。
- 未运行全量测试；本报告只依据上述新鲜聚焦测试和回归测试。

## P1 fix round 1

使用修复后的 R81 实现、同一日期区间和同一快照重跑 anchor batch，P1 已解决：

- batch_id：`92ceb1c4e41b`
- R81 run_id：`23cfd2292050`
- R58 control run_id：`09c059d2f91d`
- 两者终态：`SUCCEEDED`
- `comparison_available=true`
- `comparable_variant_count=2`
- 区间：`20130329..20220729`
- snapshot：`7596807626fdf7f1aa9bdaddd84cd4575e15ac473c8331879d841ecacd941de6`
- output_root：`C:\Users\LK\.codex\visualizations\2026\09\02\01a062de-a2d8-7272-8687-0c37c9e3efdb\r81runs`

R81 指标：

- annual_return：`0.10684818002280339`
- annual_volatility：`0.12502190028359905`
- sharpe：`0.854635706067733`
- max_drawdown：`-0.31146480897784284`
- total_return：`1.5724334844560595`

本轮同时验证了 anchor 与 R58 control 的可比较性；此前因 ACL 阻塞的 anchor 残余风险已由该成功批次关闭。runner 新增 `--output-root`，允许将 `strategy_batches` 与 `runs` 产物写入指定输出根目录；未改变日期常量、PIT/data contract、执行语义或 R81 策略逻辑。
