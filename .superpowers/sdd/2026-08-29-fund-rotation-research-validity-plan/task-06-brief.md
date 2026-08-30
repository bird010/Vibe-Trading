# Task 6 实施简报：Batch 3B 多周期相对动量

## 目标

严格执行计划 Task 6：以 R39 为控制组，仅将代表基金的排名替换为等权
`rank(R60) + rank(R120) + rank(R240)`；不加入 R20，不改变 R39 的聚类、carry、
staging、top-K、执行、成本和仓位分配。

## 最小改动约束

- 只新增 R73 策略目录、注册、focused tests、Batch 3B 实验登记脚本和中文报告。
- 不修改 R39/R40、公共 Runner、execution ledger、平台架构或历史产物。
- 缺少任一周期窗口、非有限值或无法确定的 tie-break 均 fail-closed；排序必须确定。
- 先做排名相关性、rank flip、覆盖率和因果边界诊断；没有可验证 U1/R39 paired 输入时，
  报告 `unavailable`，不得伪造三折回测或晋级结论。

## 必须验证

- R60/R120/R240 等权 rank 的精确聚合、缺失窗口、ties、确定性 ordering。
- 任何 R20/短周期信号均不得进入 R73 pipeline。
- 通过 score overlay 后保留 R39 的 selected-position allocation、carry/staging、
  top-K、execution 和 cost 语义。
- 记录 rank flips、score coverage、holding period、switch count、turnover 和 fold contribution。
- Task 完成后运行 focused、R39/R40/Runner/catalog 回归、实验脚本、hash/diff 检查，并由
  独立 `gpt-5.6-luna/high` review；P0/P1 非零不得进入 Task 7。
