# Task 5 实施简报：Batch 3A 绝对动量门

## 目标

严格执行计划 Task 5：在冻结 U1 / R39 控制边界上只增加 `R126d > 0` 绝对动量门，
形成独立策略 `ai_rotation_r72_r39_absolute_momentum`。通过门禁的候选保持 R39 原目标，
失败候选只进入现金；不得改变 R39 的选择、聚类、carry、staging、执行和仓位分配。

## 最小改动约束

- 只新增 R72 策略目录、注册、focused tests、Batch 3A 实验登记脚本和中文报告。
- 不修改 R39/R40 策略逻辑、公共 Runner、execution ledger、平台架构或历史产物。
- 缺少 R126d 窗口、未来字段可见、无效/非有限数值均 fail-closed，并使用独立 reason code。
- 先完成 RED 测试，再实现最小 overlay；不存在可验证输入时报告 `unavailable`，不得伪造 paired backtest。

## 必须验证

- 正、负、零和不足 126 日窗口的判定边界。
- signal cutoff、T+1/T+2 可见性与 future-data exclusion。
- 通过门禁时 R39 target 完全不变；失败时仅释放为现金，不重分配。
- 三折 paired 实验、压力场景、覆盖率和尾部风险指标；缺数据时明确停止晋级。
- Task 完成后运行 focused、R39/R40/Runner 回归、实验脚本、hash/diff 检查，并由独立 `gpt-5.6-luna/high` review；P0/P1 非零不得进入 Task 6。
