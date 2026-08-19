# StockPred 回测正确性与批处理可靠性修复设计

## 1. 目标

修复 StockPred 当前分支中已确认的七项正确性与可靠性问题：

1. 受成交容量限制的卖出不能持续平仓。
2. 日线 StockPred 数据源把非日线请求静默降级为日线。
3. 相关性实验将同期收益误作前瞻收益。
4. 策略批次创建成功后，首次详情查询失败会被 UI 误报为创建失败。
5. `stalled` 批次的 SSE 流不发送终态、不关闭。
6. 逐股 OHLCV 详情发生中途写入失败后会永久处于部分完成状态。
7. 策略批次页面的 Parity/Research 模式选择没有进入后端契约和执行配置。

本设计只改变上述行为及其测试；不重构未涉及的回测资金模型、不改变已有图策略的 Parity 默认参数，也不迁移历史批次文件。

## 2. 第一性约束

这批修复涉及回测与投资研究结论，必须遵守以下不可变事实：

- 一笔持仓在全部卖出前仍属于组合风险敞口；容量限制不能使剩余仓位“消失”。
- 日线数据没有任何分钟或小时信息，不能以成功响应的形式伪装为分钟/小时数据。
- 在 T 日可见的数据上计算的因子，只能与 T 之后的收益配对；`T-1 → T` 是已发生的同期收益，不是前瞻收益。
- HTTP `202` 已表示批次已被接受；后续读取失败不能改变其创建结果或诱导用户重复提交。
- 流式客户端必须收到明确终态；否则重连、关闭与失败呈现无法正确决策。
- 多文件详情只有在完整集合成功发布后才可见；“存在任意一个文件”不能代表事务完成。
- UI 可编辑的模式必须影响持久化请求和实际运行配置；否则界面承诺与结果不一致。

## 3. 范围、职责与文件

| 领域 | 修改文件 | 责任 |
|---|---|---|
| 回测卖出 | `agent/backtest/stockpred_graph/execution.py` | 在后续可交易日持续尝试卖出残余数量。 |
| 回测卖出测试 | `agent/tests/stockpred/test_execution.py` | 验证容量受限后的完整/期末残仓语义。 |
| 周期契约 | `agent/backtest/loaders/stockpred_loader.py` | 显式拒绝非 `1D` 周期。 |
| 周期契约测试 | `agent/tests/test_stockpred_loader.py` | 验证 `1D` 保持可用，`1m`/`1H` 被拒绝。 |
| 实验前瞻收益 | `agent/scripts/correlation_experiment.py` | 使用 T 后下一交易日的复权收盘价计算收益。 |
| 实验测试 | 新建或扩展 `agent/tests/stockpred/test_correlation_experiment.py` | 在极小伪网关上验证日期对齐和收益方向。 |
| 批次模式契约 | `agent/src/stockpred/strategies/contracts.py`、`agent/src/stockpred/strategy_execution.py` | 将模式持久化并传入每个策略运行配置。 |
| API/SSE | `agent/src/api/stockpred_routes.py` | 将 `stalled` 作为终态事件；创建 API 保持 `202` 语义。 |
| 详情物化 | `agent/src/stockpred/strategy_detail.py`、`agent/src/ui_services.py` | staging 写入、完成标记、失败后完整重试。 |
| 前端批次交互 | `frontend/src/lib/api.ts`、`frontend/src/pages/StockPred.tsx` | 传递模式；POST 成功后立即跟踪批次；详情 GET 独立失败。 |
| 前端测试 | `frontend/src/pages/__tests__/StockPred.test.tsx` | 验证创建、模式与 SSE 终态行为。 |

## 4. 设计一：容量受限卖出的连续执行

### 4.1 当前根因

`execute_target_portfolio()` 仅针对 `simulate_trades()` 找到的首个可卖日期生成一条 `SELL` 事件。若 `apply_capacity_limit()` 返回 `PARTIAL`，账本只卖出该条事件的 `qty`，剩余数量没有后续事件，因此永远停留在 `holdings`。

### 4.2 新行为

保持 `execute_target_portfolio()` 的公开签名与 `TRADE_COLUMNS` 不变。对每个已买入标的：

1. 从既有的首个可卖日期开始，按 `trade_date` 升序扫描该标的后续市场行，直到该标的数量为零或市场数据结束。
2. 只在可卖行下单：有效开盘价、正成交量，且开盘价未封跌停。不可卖日不生成伪成交，也不减少剩余数量。
3. 每个可卖日以 `remaining_qty × 当日执行价` 作为 `requested_value`，再调用 `apply_capacity_limit()`。
4. `executed_value` 和 `qty` 以当日容量为上限；若仍有剩余，记录 `PARTIAL` 并继续下一日；全部卖完则记录 `FILLED` 并停止。
5. 若扫描到市场末尾仍有剩余，最后一条已成交事件仍保留 `PARTIAL`。不会人为创建 `FILLED`，期末账本会以最后可用价格标记该真实残仓。
6. 每条卖出事件沿用原始 `signal_date`；`exit_delay_days` 表示该事件相对目标退出交易日的实际延迟，而不是相对上一笔卖单的延迟。

不在本任务中解决不同调仓周期之间的现金复用/杠杆问题；该问题需要跨批次订单净额化和组合级资金状态，超出本次用户指定范围。

### 4.3 TDD 测试

先在 `test_execution.py` 新增以下失败测试：

```python
def test_capacity_limited_sell_retries_on_next_sellable_day() -> None:
    market = _market().assign(amount=10.0)
    # 追加一个下一交易日，足以成交剩余仓位。
    market = pd.concat([market, market.tail(1).assign(trade_date="20250108")])
    trades = execute_target_portfolio(
        market, _targets(), signal_date="20250102", holding_days=1,
        capital=1_000_000, max_participation=0.05,
    )
    sells = trades[trades["side"] == "SELL"]
    assert list(sells["status"]) == ["PARTIAL", "FILLED"]
    _, equity = build_daily_ledger(trades, market, initial_capital=1_000_000)
    assert equity.iloc[-1]["market_value"] == pytest.approx(0.0)
```

另加数据结束测试：容量不足且无下一日时，最后 `SELL` 必须为 `PARTIAL`，账本仍保留正的 `market_value`；该测试禁止实现通过伪造全额成交来“修复”。

## 5. 设计二：StockPred loader 的周期契约

### 5.1 新行为

`DataLoader.fetch()` 在 `validate_date_range()` 后、任何 Lance 读取前规范化 `interval`。只接受大小写无关的 `"1D"`；其他值抛出 `ValueError("stockpred loader supports only interval='1D'")`。

异常必须发生在现有读取异常捕获块之外：调用者能明确知道请求不受支持，而不是收到空数据并误以为没有行情。`fields` 继续保持现有“固定 OHLCV schema”的行为。

### 5.2 TDD 测试

在 `test_stockpred_loader.py` 先新增参数化失败测试，并在实现前运行：

```python
@pytest.mark.parametrize("interval", ["1m", "1H", "4H"])
def test_fetch_rejects_non_daily_interval(loader, interval: str) -> None:
    with pytest.raises(ValueError, match="only interval='1D'"):
        loader.fetch(["000001.SZ"], "2024-01-01", "2024-01-31", interval=interval)
```

保留/补充 `interval="1D"` 与 `interval="1d"` 的成功测试，确保没有改变日线读取与日期过滤。

## 6. 设计三：相关性实验的真正前瞻收益

### 6.1 当前根因

`run_phase2d_ic_from_stockpred()` 从截至 `eval_date` 的面板读取最后两行 close，并计算 `close[T] / close[T-1] - 1`。因子也是以该面板最后一行 T 计算，故该收益已发生，不能用于“因子预测下一期收益”的 IC。

### 6.2 新行为与接口

在实验脚本中提取纯函数：

```python
def forward_returns_on_next_trade_day(
    prices: pd.DataFrame,
    factors: pd.DataFrame,
    eval_date: str,
) -> pd.Series:
    """返回 factor 截面所对应标的从 T 收盘到下一个交易日收盘的收益。"""
```

实现规则：

1. 交易日历由 `gateway.trade_dates()` 获得，包含 `STOCKPRED_END` 之后至少一个实际交易日；若 `eval_date` 无下一交易日，跳过该截面。
2. 用固定快照的 `gateway.prices()` 和 `gateway.adjustment_factors()` 读取 T 与 next-T 两日，并以现有 `apply_qfq()` 取得复权 close。
3. 仅对同时拥有 T、next-T 价格且因子值有限的标的计算 `close[next_T] / close[T] - 1`。
4. 以因子截面索引和收益截面交集计算 Spearman IC；少于 30 个有效标的则跳过。
5. 输出元数据新增 `return_horizon="next_trade_day"` 与每个策略有效 IC 截面数，避免将不同收益定义的结果混合。

实验脚本不得再次使用 `panel["close"].iloc[-2:]` 计算收益。面板仍只负责生成 T 时点可见的因子。

### 6.3 TDD 测试

使用两只股票、三个交易日的伪价格数据：T 为 `20250102`，next-T 为 `20250103`。断言函数返回 `(close_0103 / close_0102) - 1`，并断言不会使用 `20250101 → 20250102`。

再覆盖：无下一交易日、缺失任一价格、因子/价格索引不相交时均返回空序列，调用方跳过而非填充零收益。

## 7. 设计四：批次创建、模式和 SSE 终态

### 7.1 模式契约

在 `StrategyBatchRequest` 新增：

```python
mode: Literal["parity", "research"] = "parity"
```

在 `StrategyBacktestConfig` 新增同名字段，并使 `StrategyReportExecutor` 的单策略、Alpha 批量两条构造路径均从 `request.mode` 赋值。`StrategyRunStore._context()` 记录 `mode`，以便运行详情、比较键与追溯信息保持一致。

Parity 模式的固定参数由既有策略批次产品规则统一校验：实施者必须明确现有规则是“策略批次只支持研究参数”还是“Parity 锁定图策略同样的参数”，并把该规则放在 `StrategyBatchRequest` 的模型校验中，而不是仅靠前端禁用控件。Research 模式允许请求内已有的 `top_n`、`eval_step`、`forward_days`。

前端 `StrategyBatchRequest` TypeScript 类型增加 `mode`，`startStrategyBatch()` 将 `form.mode` 传入 POST body。

### 7.2 POST 成功与首次 GET 失败的分离

前端创建流程拆为两个阶段：

1. `createStrategyBatch()` 成功后立即将 `created.batch_id` 记为当前活动批次，关闭旧流、创建新 `EventSource`，并调用 `pollRunningBatches()`；此后不得把创建状态改为失败。
2. `getStrategyBatch()` 是独立的补充读取。成功时更新详情和 recent 列表；失败时只显示“详情暂不可读/将自动重试”的非阻塞状态，SSE `progress` 的 `refresh()` 仍可稍后恢复详情。

可使用只含 `batch_id`、`status: "queued"`、空 `reports` 的本地占位摘要，确保用户能看到新批次且不会再次点击启动。占位对象一旦读到真实摘要即被替换。

### 7.3 stalled SSE

后端 `iter_strategy_batch_events()` 将 `stalled` 纳入终态：先发出状态内容，再发 `event: error`（payload 为同一 state，包含 `status="stalled"`），随后 `return`。`completed`/`completed_with_failures` 继续发 `done`。

前端只在显式 `done` 或服务器发出的 `error` 事件后关闭流。原生 EventSource 网络 `error` 不关闭流，只保留可恢复提示，让浏览器完成自动重连。若两种 `error` 事件需要区分，服务器终态改用命名事件 `batch_error`，前端监听 `batch_error` 并关闭，原生 `source.onerror` 则不关闭。

本设计采用 `batch_error`，避免浏览器原生错误与服务端同名事件歧义。

### 7.4 TDD 测试

后端先新增：

```python
async def test_stalled_batch_sse_emits_batch_error_and_stops() -> None:
    # 伪造 state={"status": "stalled"}，收集异步生成器。
    # 断言依次含 progress 和 event: batch_error，随后 StopAsyncIteration。
```

前端先新增：

1. mock POST 成功、首个 GET 失败；断言错误文案不是“启动失败”、POST 只调用一次、`EventSource` 已使用返回的 batch id 创建。
2. 选择 `research` 并开始；断言 POST body 包含 `mode: "research"`。
3. 触发 `source.onerror`；断言未调用 `close()`。触发命名 `batch_error`；断言调用 `close()` 并刷新批次摘要。

## 8. 设计五：OHLCV 详情的可恢复发布

### 8.1 发布协议

在每个策略运行目录增加 `detail_complete.json`，它是唯一的详情提交标记。其内容至少包括：

```json
{
  "version": 1,
  "codes": ["000001.SZ"],
  "detail_manifest_sha256": "..."
}
```

`detail_manifest_sha256` 是原始 `detail_manifest.json` 的规范化 JSON SHA-256。完成标记有效的条件为：版本一致、代码集合与当前 manifest 完全一致、摘要一致、且 `artifacts/ohlcv_<code>.csv` 对每个代码都存在。

物化流程：

1. 读取并校验 screening、数据快照与 detail manifest。
2. 若完成标记有效，直接返回，保持幂等。
3. 在运行目录的 `.detail.staging/` 写入全部 `ohlcv_<code>.csv`。任何失败都删除 staging，不写完成标记。
4. 所有 staging 文件写成功后，逐个替换 `artifacts/` 内同名文件；随后原子写入 `detail_complete.json` 作为提交点。
5. 在第 4 步中断时可能留下旧或部分新 CSV，但没有有效完成标记；下一次调用必须忽略这些文件、重新写入完整集合并最终提交。

不替换整个 `artifacts/` 目录，因为其中包含 screening 阶段已提交的 metrics、trades、signals 等文件。

### 8.2 UI 读取规则

`load_price_series()` 对含 `detail_manifest.json` 的策略运行，只有在完成标记有效后才读取 `ohlcv_*.csv`；否则先调用懒物化。普通图策略/历史非策略运行没有 detail manifest，继续沿用已有 `ohlcv_*.csv` 读取逻辑。

`_try_materialize_detail()` 可以吞掉对 UI 不可恢复的 I/O 异常并返回 `False`，但不得将部分 CSV 当成成功结果；详细异常必须保留在服务端日志。

### 8.3 TDD 测试

在 `test_strategy_detail.py` 新增：

1. 多标的物化时让第二次 CSV 写入抛 `OSError`：断言没有 `detail_complete.json`，并且下一次正常调用会补齐全部标的并写入完成标记。
2. 人工留下单个 `ohlcv_*.csv`、不创建完成标记：断言调用会重建完整集合，而不是提前返回。
3. 完成标记中的代码或 manifest 摘要不匹配：断言被视为未完成并重新发布。

在 `test_run_analysis.py` 新增：含 detail manifest 且只存在部分 CSV 时，`load_price_series()` 不返回部分数据；物化成功后返回完整数据。

## 9. 实施顺序与 TDD 门禁

实施者必须按下列顺序执行；每项都先写失败测试、确认失败原因是旧行为，再写最小实现并运行关联测试：

1. 卖出残仓连续执行。
2. loader 周期拒绝。
3. 前瞻收益函数与实验接入。
4. 模式契约（Python 与 TypeScript）。
5. `stalled` SSE 与前端命名终态事件。
6. POST/首次 GET 解耦。
7. OHLCV staging、完成标记和 UI 完整性检查。

每一项完成后至少运行其测试文件；所有项完成后运行：

```powershell
python -m pytest agent/tests/stockpred/test_execution.py agent/tests/test_stockpred_loader.py agent/tests/stockpred/test_batch_api.py agent/tests/stockpred/test_strategy_detail.py agent/tests/stockpred/test_run_analysis.py -q
python -m pytest agent/tests/stockpred/test_contracts.py agent/tests/stockpred/test_strategy_runner.py -q
cd frontend
npm run test:run -- src/pages/__tests__/StockPred.test.tsx
npm run build
```

如果运行环境仍无法访问默认 pytest 临时目录，测试命令必须指定一个经验证可写的 `--basetemp`；不得把权限错误误判为代码通过或失败。

## 10. 验收标准

1. 容量受限的卖出在后续可卖日持续执行；有足够后续容量时期末持仓为零。
2. 非 `1D` StockPred loader 请求明确失败，不返回日线伪结果。
3. 实验 IC 的收益严格为 T 到 next-T，且无 next-T 的截面不参与统计。
4. POST 返回 `202` 后，即使首次详情 GET 失败，页面仍显示已创建批次、保持一个流连接且不会提示“启动失败”。
5. `stalled` SSE 至多发送一次终态 `batch_error` 并关闭；普通网络短断不会被前端主动关闭。
6. 多标的详情只有在全部 CSV 与完成标记一致时被 UI 使用；失败重试可恢复完整集合。
7. 选择 Research 后，持久化 `request.json`、运行 `config.json` 和详情 context 都反映 `mode="research"`；Parity 参数限制由后端模型校验执行。
