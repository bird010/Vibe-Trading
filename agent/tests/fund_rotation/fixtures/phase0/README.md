# Phase 0 Golden 基线与差异许可清单

本目录冻结 **Phase 0 修复前** 的 pipeline 输出，作为回归检测基准。Phase 0
Task 2–7 引入的任何行为变化都会与 `pre_fix_golden.json` 产生 diff，由
`test_phase0_golden.py` 按设计 §35.1 的逐字段容差检测。

## 数据集（`build_golden_data`，seed=20260802）

- 80 周合成日线（2022-01-03 起，每周 5 个交易日）；
- 10 只标的：`510300.SH` 基准 + 9 只可投资 ETF（`510010.SH`–`510090.SH`）；
- **一次复权事件**：`510010.SH` 的 `adj_factor` 自第 65 周周一起由 1.0 变为 2.0
  （此时该 ETF 已被持有，触发执行循环的公司行为份额调整路径）；
- **一个缺失收盘价**：`510020.SH` 在第 30 周周五（周末观察点）OHLCV/成交额为 NaN
  （影响该周收益，覆盖缺失值语义路径）。

## 配置（`build_config`）

`k=4, top_n=2, min_training_weeks=52, correlation_lookback_weeks=52,
min_valid_weeks=20, min_pairwise_weeks=20, recluster_interval_weeks=26,
momentum_window_weeks=4, initial_capital=1_000_000,
start_date=20220101, end_date=20230720`。

采用真实 52 周回看，使 golden 对 Task 4 的首个信号边界修正敏感；产生 28 个调仓周、
2 次重聚类、约 785 笔订单（含 BUY/SELL 的 FILLED/PARTIAL/BLOCKED 与 1 次
CORPORATE_ACTION）。

## 许可差异清单（仅此五类，其他离散事件不得变化）

Phase 0 修复后，相对本 golden 仅允许出现以下来源的差异，并须逐项记录到
`approved_delta.json`（Task 8 建立）：

1. **首个可用 52 周窗口**（Task 4）：首个完整 52 周收益窗口的边界修正可能移动首个
   信号/调仓日；
2. **缺失值不前填**（Task 3）：`pct_change(fill_method=None)` 显式化后，缺失周收益
   不再被隐式前填，可能改变受缺失影响的收益/相关性/目标；
3. **预评价目标首日执行**（Task 6）：评价区间开始前最近一次 `SET_TARGETS` 改为在
   正式评价首个交易日开盘执行；
4. **完整评价日历**（Task 5/7）：净值/指标覆盖完整正式评价日历（含空目标、全阻塞、
   无成交情形），不再依赖首笔成交或日期交集；
5. **首期收益/指标**（Task 5）：指标以独立 `initial_nav=1.0` 计算首期收益，日终净值
   不伪造首日 1.0。

**除上述五类之外，不允许任何离散事件变化**（日期、代码、动作、原因码、订单方向、
状态、簇标签、整数份额）。浮点字段不得通过放宽容差掩盖交易数量或费用差异。

## 容差规则（设计 §35.1）

| 字段类别 | 验收标准 |
|---|---|
| 日期、代码、动作、原因码、订单方向、状态、簇标签、整数份额 | 完全一致 |
| 目标权重 | `rtol=0, atol=1e-12` |
| 成交价格、佣金、现金、权益金额 | `rtol=1e-12, atol=1e-6` |
| 归一化 NAV | `rtol=1e-10, atol=1e-10` |
| 收益风险指标 | `rtol=1e-9, atol=1e-10` |

## 再生 golden

仅在 Phase 0 任何代码修改 **之前** 再生（或在 Task 8 记录新批准基线时）：

```powershell
$env:PHASE0_REGEN="1"
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation\test_phase0_golden.py -q
Remove-Item Env:PHASE0_REGEN
```
