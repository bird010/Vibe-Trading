# StockPred Graph 在 Vibe-Trading 中的运行说明

本文档记录 Vibe-Trading 侧调用 StockPred Graph 的配置、启动、回测、API/SSE、产物和排错口径。

## 前置配置

必须先配置 StockPred 数据根目录：

```powershell
$env:STOCKPRED_DATA_ROOT = "E:\code\stock\StockPred"
```

建议在本地验证时把 run 产物写到临时目录，避免污染仓库：

```powershell
$env:VIBE_TRADING_RUNS_DIR = "$env:TEMP\vibe-stockpred-runs"
```

状态检查：

```powershell
E:\anaconda3\envs\VibeTrading\Scripts\vibe-trading.exe stockpred status --json
```

## CLI

parity 模式锁定 frozen Oracle 参数：

- `top_n=50`
- `eval_step=5`
- `forward_days=5`
- `benchmark_code=000300.SH`
- `portfolio_capital=10000000`
- `max_participation=0.05`
- market scope：`SSE/SZSE`

normal golden 示例：

```powershell
E:\anaconda3\envs\VibeTrading\Scripts\vibe-trading.exe stockpred graph-backtest `
  --start 2025-01-02 `
  --end 2025-03-31 `
  --mode parity `
  --parity-golden C:\Users\LK\AppData\Local\Temp\vibe-stockpred-golden-normal-20260702 `
  --json
```

research 模式可解锁 `top_n`、`eval_step` 等选择参数，用于 Web 页面上的探索性回测。

## API / SSE

后端路由前缀为 `/stockpred`：

- `GET /stockpred/status`：检查 StockPred 数据和 contract。
- `GET /stockpred/graph/defaults`：读取 Graph 默认配置和 parity 锁定字段。
- `POST /stockpred/graph/backtests`：启动 Graph 回测。
- `GET /stockpred/graph/backtests`：列出最近回测。
- `GET /stockpred/graph/backtests/{run_id}/events`：SSE 进度事件。

前端页面为 `/stockpred`。Vite 代理对 `/stockpred` 同时支持 SPA HTML fallback 和 API 代理，避免刷新页面时误代理到后端 API。

## Run artifacts

Vibe run 目录包含：

- `config.json`
- `data_snapshot.json`
- `model_manifest.json`
- `run_card.json`
- `run_card.md`
- `state.json`
- `parity.json`（仅有 golden 时写入）
- `artifacts/signals.parquet`
- `artifacts/selected_signals.csv`
- `artifacts/trades.csv`
- `artifacts/positions.csv`
- `artifacts/equity.csv`
- `artifacts/metrics.csv`
- `artifacts/ohlcv_{code}.csv`

Web RunDetail 使用完整 Vibe artifacts；parity comparator 另有 Oracle-compatible view，不替换 Web 展示层。

## Parity 口径

Vibe 保留两套语义：

1. Vibe artifacts：完整信号、逐日 ledger、K 线和买卖点，用于 Web 观察策略。
2. Oracle parity view：按 frozen Oracle 的 `details/selected/trades/equity/metrics` 口径比较 golden。

Oracle parity view 的关键规则：

- forward market 截止日使用 `last_eval + forward_days * 2 calendar days`，与 frozen Oracle 对齐。
- `signals` 比较 `details.parquet` 的可执行 forward-return 行。
- `selected/trades/equity/metrics` 按 frozen Oracle exporter 的 Top-N、交易事件和累计收益语义生成。
- `signals` 全市场诊断层允许少量列级容差；`selected/trades/equity/metrics` 仍承担策略结果硬校验。

## 常见错误码

- `STOCKPRED_ROOT_MISSING`：未配置 `STOCKPRED_DATA_ROOT` 或目录缺失。
- `STOCKPRED_LANCE_UNAVAILABLE`：当前 Python 环境缺少 Lance 依赖。
- `STOCKPRED_TABLE_MISSING`：必需 Lance 表不存在。
- `STOCKPRED_SCHEMA_MISMATCH`：表字段不满足 contract。
- `STOCKPRED_READ_FAILED`：Lance 读取失败。
- `STOCKPRED_FILTER_INVALID`：日期或代码过滤条件不安全/非法。
- `STOCKPRED_EVAL_DATE_CLOSED`：评估日不是交易日。
- `STOCKPRED_ADJUSTMENT_COVERAGE`：复权覆盖率低于阈值。
- `STOCKPRED_VALID_EVAL_RATIO`：有效评估日比例低于阈值。
- `STOCKPRED_GOLDEN_MISSING`：指定 golden 目录不存在。
- `STOCKPRED_PARITY_FAILED`：至少一个 parity layer 未通过。

## 排错顺序

1. 先跑 `stockpred status --json`，确认数据根、Lance 表、schema 和 watermark。
2. 如状态慢或超时，检查 snapshot watermark 是否使用 Arrow columnar max，而不是整列 `to_pylist()`。
3. 如 Lance 在 Windows 下栈溢出，检查代码过滤是否使用 `IN (...)`，避免大量 `OR` 链。
4. 如复权覆盖率异常，先看 universe 是否误纳入 BSE 或退市名称。
5. 如 parity key 多出最后一期，检查 forward market 截止日是否与 Oracle 一致。
6. 如只有 `signals` 诊断层微小差异，但 `selected/trades/equity/metrics` 全过，优先判断是否为非 Top-N 边界分数漂移。

## 2026-07-07 验证记录

- 单元测试：`agent/tests/stockpred`，94 passed，1 个 FastAPI/TestClient deprecation warning。
- Lint：`ruff check --no-cache agent/src/stockpred agent/backtest/stockpred_graph agent/tests/stockpred`，passed。
- normal golden 完整 CLI：`graph_20260707T161617_b8956750`，status success。
- pit-boundary golden：run `graph_20260707T162300_29f02703` 的 artifacts 在当前 comparator 下重建比较 passed。
- execution-edge golden：run `graph_20260707T163354_d564a35b` 的 artifacts 在当前 comparator 下重建比较 passed。
