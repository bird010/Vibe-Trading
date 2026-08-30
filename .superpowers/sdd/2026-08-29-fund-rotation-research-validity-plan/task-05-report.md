# Task 5 实现报告：Batch 3A 绝对动量门

## 结论

已完成 Batch 3A 的最小单变量实现：新增 R72 overlay，在 R39 已生成目标之后，
只增加 `R126d > 0` 绝对动量门。通过门禁的候选保持原始 R39 target，失败候选释放为
现金，不重排、不重分配幸存目标。缺少可验证冻结 U1、R39 paired control 和因果行情
输入，因此实验登记状态为 `UNAVAILABLE_INPUTS`，未伪造回测结果，也不允许晋级。

## 修改文件与最小改动

- `.superpowers/sdd/2026-08-29-fund-rotation-research-validity-plan/task-05-brief.md`
- `agent/backtest/fund_rotation/strategies/ai_rotation_r72_r39_absolute_momentum/`
- `agent/backtest/fund_rotation/strategies/ai_rotation_r72_r39_absolute_momentum/__init__.py`
- `agent/backtest/fund_rotation/strategies/ai_rotation_r72_r39_absolute_momentum/strategy.py`
- `agent/backtest/fund_rotation/strategies/registry.py`
- `agent/tests/fund_rotation/test_ai_rotation_r72_r39_absolute_momentum.py`
- `agent/tests/fund_rotation/test_fund_rotation_catalog_api.py`
- `agent/tests/fund_rotation/test_strategy_catalog.py`
- `experiments/fund_rotation_research_validity/batch_3a_absolute_momentum.py`
- `experiments/fund_rotation_research_validity/batch_3a/manifest.json`
- `experiments/fund_rotation_research_validity/batch_3a_report.md`

未修改 R39/R40、公共 Runner、execution ledger、平台架构或历史实验产物。R72 通过
继承 R39 session/strategy，仅在已有目标生成后叠加门禁，并将 artifact/session 状态
延迟到最终门禁结果后写回；不存在额外抽象或大规模重构。

## TDD 与验证

- RED：R72 模块不存在时测试按预期在 collection 阶段失败。
- focused：R72 边界与 session 集成测试通过，覆盖正/负/零/不足窗口、内部非有限/非正
  价格、signal cutoff、T+1/T+2 排除、R39 目标保持、失败转现金和最终 artifact/session 状态。
- 精确回归命令：`E:\\anaconda3\\envs\\AlphaFin\\python.exe -m pytest -p no:cacheprovider agent/tests/fund_rotation/test_ai_rotation_r72_r39_absolute_momentum.py agent/tests/fund_rotation/test_fund_rotation_catalog_api.py agent/tests/fund_rotation/test_ai_rotation_r39_incumbent_carry.py agent/tests/fund_rotation/test_ai_rotation_r40_single_name_ceiling.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_strategy_catalog.py -q --tb=short --basetemp=tmp\\pytest_task5_final6`。
- 在当前工作区授权环境中上述精确回归：`93 passed`、退出码 0；另有 3 个既有 Pydantic warning。
- 独立 reviewer 环境同组测试为 `81 passed、8 errors、退出码 1`，8 项均在 pytest `tmp_path` setup/cleanup 阶段触发 Windows ACL `WinError 5`，未进入业务断言；因此不将该环境写作整体通过。
- 登记脚本精确命令：`E:\\anaconda3\\envs\\AlphaFin\\python.exe experiments/fund_rotation_research_validity/batch_3a_absolute_momentum.py`，连续两次退出码均为 0，manifest/report hash 未变化。
- Batch 3A 登记脚本：连续运行两次成功；manifest 和报告 hash 均保持一致。
- `git diff --check`：通过。

## 产物与 hash

- `experiments/fund_rotation_research_validity/batch_3a/manifest.json`
  - SHA-256：`bc0d544097c9b301c18ba65e1dd005d5d926074553f9cdabf50f631bb00b7328`
- `experiments/fund_rotation_research_validity/batch_3a_report.md`
  - SHA-256：`150b6aeb2f78f6c08e2c22433ef1a8cb081dfe2030c5a8cc03eac937bcc911a6`
- R72 源文件 SHA-256：`e4441f4bed9d2458f2d23152d72f635e6c33d83bd11a3b6a7aaa027858f57818`
- R39 控制源文件 SHA-256：`9f9bcc49494adeb5e54f169b85605a078027098b9db3281fa895f18bf1c5d72c`

manifest 同时记录 R72 策略依赖闭包 hash、闭包内逐文件 hash 和 Batch 3A 登记脚本
hash；源码或依赖变化时旧 manifest 会 fail-closed。R72 requirements 的 warmup 已至少
提升到 127 个交易日。

manifest 明确记录三折回测为 `0/3`、压力场景和 CAGR/Sharpe/MDD/CVaR/worst 3M、现金
占比、switch、持有期、换手、成本后收益均为 `unavailable`，`promotion_allowed=false`。

## 独立 review 状态

前五次独立 `gpt-5.6-luna` 高推理 review 分别发现
`P1=3/P2=4/P3=2`、`P1=3/P2=1/P3=0`、`P1=0/P2=4/P3=0`、
`P1=2/P2=2/P3=0` 和 `P1=2/P2=2/P3=0`；第六次独立 review（模型
`gpt-5.6-luna`、reasoning `high`）审查 head `12c5d4c7`，结果为
`luna_review_result=PASS`，`p0_count=0`、`p1_count=0`、`p2_count=1`、`p3_count=0`，
`decision=PROCEED_TO_TASK_6`。剩余 P2 为变更清单展开建议，不阻断下一步；Batch3A 自身
仍为 `UNAVAILABLE_INPUTS` 且不晋级。
