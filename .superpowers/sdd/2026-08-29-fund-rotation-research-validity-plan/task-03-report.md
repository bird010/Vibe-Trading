# Task 3 实现报告：容量感知代表解锁与 fallback challenger

## 范围与结论

新增 `ai_rotation_r71_r39_capacity_aware_representative`，以最小 overlay 继承 R39：
只在决策 cutoff 可见、同簇同身份且容量证据明确足够时切换代表；原代表容量足够时
carry；候选按 score 降序、代码升序确定性 fallback；所有候选不可用时只将当前代表
释放为现金，其他 R39 目标保持不变。未知/非法容量、未来成交量和不可交易状态均
fail-closed。

未修改 R39、公共 Runner、execution ledger 或平台级架构；既有 `capacity.py` 的
ADV20/滑点接口保持兼容。

## 修改文件

- `agent/backtest/fund_rotation/capacity.py`
- `agent/backtest/fund_rotation/strategies/ai_rotation_r71_r39_capacity_aware_representative/__init__.py`
- `agent/backtest/fund_rotation/strategies/ai_rotation_r71_r39_capacity_aware_representative/strategy.py`
- `agent/backtest/fund_rotation/strategies/registry.py`
- `agent/tests/fund_rotation/test_capacity.py`
- `agent/tests/fund_rotation/test_ai_rotation_r71_r39_capacity_aware_representative.py`
- `agent/tests/fund_rotation/test_strategy_catalog.py`
- `experiments/fund_rotation_research_validity/batch_2_capacity_repair.py`
- `experiments/fund_rotation_research_validity/batch_2/manifest.json`
- `experiments/fund_rotation_research_validity/batch_2_report.md`

## TDD 与验证

- 计划指定 RED：新 API/模块不存在时按预期收集失败。
- focused + R39/R40/Runner/catalog 回归：`95 passed`，退出码 0。
- Batch 2 默认脚本：退出码 0；第二次复跑验证不可变 manifest/report 幂等。
- `git diff --check`：通过；仅有 Git 对既有 CRLF 文件的提示。

Focused 命令：

```text
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH='agent;agent/src';
E:\anaconda3\envs\AlphaFin\python.exe -m pytest -p no:cacheprovider
  agent/tests/fund_rotation/test_capacity.py
  agent/tests/fund_rotation/test_ai_rotation_r71_r39_capacity_aware_representative.py
  agent/tests/fund_rotation/test_strategy_catalog.py
  agent/tests/fund_rotation/test_ai_rotation_r39_incumbent_carry.py
  agent/tests/fund_rotation/test_ai_rotation_r40_single_name_ceiling.py
  agent/tests/fund_rotation/test_runner_contract_integration.py -q
结果：95 passed，退出码 0
```

## 实验产物与证据边界

- manifest：`experiments/fund_rotation_research_validity/batch_2/manifest.json`
  - SHA-256：`7d7dc7d03c063c0f0f880b4a3d140f9edc739687eaf8c035edb97807c917aabe`
- 中文报告：`experiments/fund_rotation_research_validity/batch_2_report.md`
  - SHA-256：`c083e925a44735592e96e4502c56dc7de9ff5336eab60ad43306603152be1ec3`

当前仓库没有可验证的冻结 U1 manifest、历史 counterfactual 输入或正式 paired
backtest 输入。因此 blocked attempt ratio、parent fill ratio、capacity-zero、
未成交机会成本、成交后换手、目标偏离持续天数，以及 CAGR/Sharpe/MDD 的 1×/2×/3×
压力测试均记录为 `unavailable`；`promotion_allowed=false`。本 Task 没有填零、
重放旧账户或声称 U1 晋级。

## 最小改动自评与独立 review 状态

新增代码只覆盖容量估计、确定性代表选择、R39 overlay、显式注册、测试和证据登记。
为修复一个边界，补充了“多目标组合中当前代表不可用时不清空其他目标”的回归测试。
没有顺手重构相邻策略或公共执行路径。

独立 review 前五轮发现 5 个 P1 与 4 个 P2；已修复容量溢出、score=0 排序、
候选可见性/已知时间/容量参数 fail-closed、目标 scope 锚定、anti-flap 状态诊断，
以及多目标组合 cash fallback 不清空其他目标；第二轮进一步使 anti-flap 对非零部分容量
形成实际保留约束，并使硬阻塞仍强制解锁；显式 target scope 不再被 prior 覆盖。R71 的容量 observation 仍是显式可选
数据边界；在现有策略 context 不暴露目标股数/账户状态时保持 unavailable，避免把执行
状态偷渡进信号层。第三、四轮进一步要求双已知时间字段、严格日期格式和 T15 cutoff、
完整 scope（含 None fail-closed）以及市场容量参数不可被候选覆盖；第五轮进一步
修复同日 volume_date 的时间语义并改为以实际 manifest 文件计算 hash。第六轮独立
`gpt-5.6-luna` 高推理复审已 PASS；允许进入 Task 4。

- `luna_model = gpt-5.6-luna`
- `luna_reasoning = high`
- `luna_review_result = PASS`
- `p0_count = 0`
- `p1_count = 0`
- `p2_count = 0`
- `p3_count = 0`
- `decision = PROCEED_TO_TASK_4`
