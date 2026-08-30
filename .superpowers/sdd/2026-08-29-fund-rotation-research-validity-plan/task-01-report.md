# Task 1 实现报告：Batch 0 证据冻结与 summary 指标合同修复

## 范围与最小改动检查

本任务只修改 summary 指标投影、正式比较的契约门禁、BatchService 的契约传递、
对应测试、Batch 0 独立修复脚本和中文报告。没有修改 R39 策略逻辑、执行账本
语义、Runner 或源运行产物；源订单、持仓、净值和成交文件只读并记录 SHA-256。

## TDD 证据

1. 初始修复测试先运行，因修复脚本尚不存在得到预期的
   `ModuleNotFoundError`（RED）。
2. 首轮最小实现后，Batch 0 投影/脚本测试为 `4 passed`（GREEN）。
3. Luna 首轮 review 指出数值有限性、源目录保护和缺失源文件三个 P1；针对
   反馈新增测试先运行，得到 `3 failed, 5 passed`（RED），失败分别对应
   `None/NaN`、源输出目录冲突和缺失源文件未 fail-closed。
4. 修复后 focused TDD 测试为 `12 passed`（GREEN）。
5. 使用仓库隔离临时目录运行包含 execution ledger、比较和 BatchService 的
   完整 Task 1 回归为 `67 passed`。
6. 修复脚本已重新执行并重新生成独立 Batch 0 产物；`git diff --check` 通过。

默认 pytest 临时目录因 Windows ACL 在创建 `tmp_path` 时出现 `WinError 5`；
使用仓库内隔离临时目录后回归测试通过，该环境问题不计为业务断言失败。

## 实现结果

- v2 执行指标逐项只接受有限数值；`None`、NaN、无穷和非数字值变为
  `None`，状态按全部不可用/部分缺失/全部可用分别为
  `unavailable`/`partial`/`available`。
- `turnover` 与 `one_way_turnover` 均来自
  `execution_diagnostics_v2.trades.one_way_turnover`。
- BatchService 将真实结果的 `metric_contract_version` 显式传入比较链路；缺失
  或 legacy 契约的 Variant 不进入正式排名。
- 修复脚本拒绝源目录本身及其子目录作为输出目录，并要求四个源文件全部存在；
  输出写入独立目录，不覆盖源摘要或原始交易产物。
- 独立产物位于
  `experiments/fund_rotation_research_validity/batch_0/`；跟踪报告位于
  `experiments/fund_rotation_research_validity/batch_0_report.md`。

## 源运行影响

源运行 `agent/runs/fund_rotation/1a8eb8560998` 的 R39 运行身份、收益、回撤、
Sharpe 和交易产物未改写。修复摘要仅把旧的 `turnover=0.0` 更正为
`one_way_turnover=41.886233690368144`，并补充年化 turnover、blocked rate、
commission、explicit fee 和 slippage opportunity cost。数据质量仍为
`RESEARCH_ONLY_UNVERIFIED_UNIVERSE`，没有被宣称为 PIT 有效证据。

## 审查状态

首轮及后续复审发现的问题均已用 RED/GREEN 测试关闭。最终独立 5.6 Luna 高推理
review：`PASS`，P0=0，P1=0，P2/P3=0，明确允许进入 Task 2。
