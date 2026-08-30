# Task 7 完成报告：Batch 3C 波动率调整排名

## 实施范围

本任务以 `ai_rotation_r39_incumbent_carry` 为控制组，新增 R74，仅将 cluster 排名分数替换为
`positive R39 momentum / annualized volatility_60`。波动率固定使用 signal cutoff 前可见的 60 个日收益，年化因子固定为 252；不引入绝对动量、不引入 inverse-volatility allocation、不改变仓位权重。

R39 的聚类、代表基金、Top-K、固定槽位、staging、incumbent carry、执行和成本语义均保持不变。cluster 任一成员缺失、价格非有限/非正、样本不足、波动率接近零（阈值 `1e-8`）或结果非有限时，分数均 fail-closed。

实现文件：

- `agent/backtest/fund_rotation/strategies/ai_rotation_r74_r39_vol_adjusted_score/`
- `agent/backtest/fund_rotation/strategies/registry.py`
- `agent/tests/fund_rotation/test_ai_rotation_r74_r39_vol_adjusted_score.py`
- `agent/tests/fund_rotation/test_strategy_catalog.py`
- `agent/tests/fund_rotation/test_fund_rotation_catalog_api.py`
- `experiments/fund_rotation_research_validity/batch_3c_vol_adjusted.py`
- `experiments/fund_rotation_research_validity/batch_3c/manifest.json`
- `experiments/fund_rotation_research_validity/batch_3c_report.md`

没有修改 R39/R40、公共 Runner、execution ledger、平台架构或历史实验产物。

## 验证结果

受影响回归命令：

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest -p no:cacheprovider agent/tests/fund_rotation/test_ai_rotation_r74_r39_vol_adjusted_score.py agent/tests/fund_rotation/test_ai_rotation_r73_r39_multi_horizon_rank.py agent/tests/fund_rotation/test_ai_rotation_r39_incumbent_carry.py agent/tests/fund_rotation/test_ai_rotation_r40_single_name_ceiling.py agent/tests/fund_rotation/test_runner_contract_integration.py agent/tests/fund_rotation/test_strategy_catalog.py agent/tests/fund_rotation/test_fund_rotation_catalog_api.py -q --tb=short --basetemp=tmp\pytest_task7_regression
```

结果：`93 passed`，3 个既有 Pydantic warning。

Batch 3C 登记脚本连续执行两次，均为 exit 0。不可变产物哈希为：

- manifest：`D0F74E54900711F0344D64B72F10EF898091BF9FB7FA9F7C05D7D24222310E40`
- report：`8B4BCEFFB5044AA2A1E53347A854BBDD3944072AB1C53385A63DA5B775169D46`

manifest 状态为 `UNAVAILABLE_INPUTS`，`promotion_allowed=false`；U1、R39 paired control、三折回测、因果行情和 forward outcomes 均不可用。因此 volatility_60/momentum/score 覆盖率、排名变化、持有期、switch、换手、阻塞率、fold contribution、收益风险指标及压力场景均明确记录为 `unavailable`，没有用零值替代，也没有做晋级声明。

## 最小改动自评

变更只覆盖 R74 score-only overlay、显式注册、focused tests、catalog API 期望同步、Batch 3C 登记脚本及中文证据报告。没有引入平台级抽象、并行账本或与本任务无关的重构；R39 控制实现保持不变。

## 独立审查门

最终独立 `gpt-5.6-luna/high` reviewer 结论为 `PASS`，P0=0、P1=0、P2=2、P3=0，允许进入 Task 8。两个非阻断 P2 为 R39 生命周期代码在 R74 中存在重复、以及 focused 测试对部分异常价格边界的持久覆盖仍可扩展；当前未观察到运行行为偏差。R74 仍因 `UNAVAILABLE_INPUTS` 保持 `promotion_allowed=false`，不得视为已晋级。
