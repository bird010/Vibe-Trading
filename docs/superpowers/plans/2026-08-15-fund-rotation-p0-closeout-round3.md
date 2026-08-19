# Fund Rotation 两个 P0 修复计划

## 目标

修复 review 附件确认的两个 P0，并用回归测试证明冻结设计中的执行与账户语义成立。

## 步骤

- [x] 核验 Native BUY lot rounding 与 Parent/OrderManager 的调用顺序
- [x] 核验 Shadow actual cash 与 signal cash 的语义边界
- [x] 增加失败回归并实施两个 P0 修复
- [x] 运行完整 fund-rotation 可信链并请子 agent 做最终 review
- [x] 完成最终验证并提交改动

## 验收标准

- BUY Parent 的 `original_requested_quantity` 在 Parent 创建前已按 PIT lot 规则取整，capacity residual 不再产生无法成交的 odd-lot。
- Shadow 保存实际 cash/NAV 结算结果，并保留 `signal_cash` 与 `execution_failure_cash` 语义；无 residual 时也不强制 actual cash weight 等于 target cash weight。
- 相关回归测试及完整 fund-rotation 测试链通过，最终 review 无新的 P0/P1 阻断。
