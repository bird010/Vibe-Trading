# Task 6 完成报告：Batch 3B 多周期相对动量排名

## 实施范围

本任务以 `ai_rotation_r39_incumbent_carry` 为控制组，新增 R73，仅替换
cluster ranking score 为等权 `rank(R60) + rank(R120) + rank(R240)`。
聚类、代表基金选择、top-K、槽位权重、staging、incumbent carry、执行与成本语义均沿用 R39。
R73 不包含 R20 或其他短周期信号；缺失窗口、非有限值和不确定输入均 fail-closed。

实现文件：

- `agent/backtest/fund_rotation/strategies/ai_rotation_r73_r39_multi_horizon_rank/`
- `agent/backtest/fund_rotation/strategies/registry.py`
- `agent/tests/fund_rotation/test_ai_rotation_r73_r39_multi_horizon_rank.py`
- `agent/tests/fund_rotation/test_strategy_catalog.py`
- `experiments/fund_rotation_research_validity/batch_3b_multi_horizon.py`
- `experiments/fund_rotation_research_validity/batch_3b/manifest.json`
- `experiments/fund_rotation_research_validity/batch_3b_report.md`

没有修改 R39/R40、公共 Runner、execution ledger、平台架构或历史实验产物。

## 验证结果

Focused 与受影响回归命令：

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest -p no:cacheprovider agent/tests/fund_rotation/test_ai_rotation_r73_r39_multi_horizon_rank.py agent/tests/fund_rotation/test_ai_rotation_r39_incumbent_carry.py agent/tests/fund_rotation/test_ai_rotation_r40_single_name_ceiling.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_strategy_catalog.py -q --tb=short --basetemp=tmp\pytest_task6_final
```

结果：`73 passed`。

Batch 3B 脚本连续执行两次，结果均为 exit 0。不可变产物哈希为：

- manifest：`3E57D9B4C00036DC697D9C03FE0E48CA004E5B96D3F27DAB7D9F63A92052F22A`
- report：`0F1411E936EC1EBFFA518D1F36D836C523444A1D966EC2D370AC4D0311A4CBFD`

manifest 状态为 `UNAVAILABLE_INPUTS`，`promotion_allowed=false`；U1、R39 paired control、三折回测、因果行情和 forward outcomes 均不可用。因此 R60/R120/R240 覆盖率、排名相关性、rank flip、持有期、switch、换手、fold contribution、收益风险指标及压力场景均明确记录为 `unavailable`，没有用零值替代，也没有做晋级声明。

## 最小改动自评

变更只覆盖 R73 score overlay、显式注册、focused tests、Batch 3B 登记脚本及中文证据报告。没有引入平台级抽象、并行账本或与本任务无关的重构；R39 控制实现保持不变。

## 独立审查门

P1 修复后新增并通过：部分成员缺失不得形成 partial cluster return；不完整 cluster 不得改变完整 cluster 的排名基数；未来 signal date 之后的行情行不得进入窗口。最终独立 `gpt-5.6-luna/high` 审查结论为 `PASS`，P0=0、P1=0、P2=0、P3=0，允许进入 Task 7。
