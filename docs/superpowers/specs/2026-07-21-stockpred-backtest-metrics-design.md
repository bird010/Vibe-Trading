# StockPred 回测报告指标设计

## 目标

在选择一个 StockPred Graph 回测报告后，于现有运行详情页显示组合总体指标，并以可排序、可展开的表格展示每个 symbol 的实际交易绩效与既有图表。

## 范围与边界

- 仅适用于 `run_context.strategy_type == "stockpred_graph"` 的运行详情；其他回测报告的现有页面不变。
- 不修改信号生成、选股、执行规则、资金分配或交易成本模型。
- 单标的统计依据实际已成交的买卖单与逐日价格重放计算；被拒绝或成交金额为零的订单不计入收益和交易次数。
- 仅在用户展开某个 symbol 后加载该 symbol 的 K 线、交易标记、技术指标和 Graph 信号，初始详情请求不加载所有图表数据。

## 指标口径

### 总体指标

组合指标从现有 `equity.csv` 的日净值曲线及 `trades.csv` 的实际成交记录计算，并写入 `metrics.csv`：

- 总收益率 `total_return`
- 年化收益率 `annual_return`
- 年化波动率 `annual_volatility`
- 最大回撤 `max_drawdown`
- 夏普比率 `sharpe`（无风险利率固定为 0）
- 索提诺比率 `sortino`（下行波动使用负日收益）
- 卡尔马比率 `calmar`
- 胜率 `win_rate`
- 盈亏比 `profit_loss_ratio`
- 交易次数 `trade_count`（已完成的买入/卖出配对数量）
- 平均持有天数 `avg_holding_days`

日收益样本不足、分母为零或不存在亏损交易时，指标值不伪造为 0：后端省略该字段，前端显示 `—`。

### 单标的指标

对每个 symbol 独立重放已成交订单，并用对应 OHLCV 收盘价逐日盯市。该独立账本的初始资金为覆盖历史最大累计净资金流出的最小金额，因此其净值曲线代表该 symbol 策略实际需要的资金与收益，而不是买入并持有基准。

每行输出与总体相同的可计算指标。表格默认按 `total_return` 降序；点击任意指标列在该列升序、降序之间切换。symbol 列按字典序排序。

## 数据与接口

### 后端产物

Graph 回测完成时：

1. 从 `trades`、`ohlcv` 和组合 `equity` 计算组合指标及单标的指标。
2. 写入已有的 `artifacts/metrics.csv`，并新增 `artifacts/symbol_metrics.csv`；每行包含 `symbol` 和数值指标。
3. 让 `run_card.json` 继续引用组合指标，不嵌入可能很大的单标的明细。

历史 Graph 报告读取详情时：优先读取 `symbol_metrics.csv`；若该文件不存在但 `trades.csv` 与 `ohlcv_*.csv` 完整，则即时计算摘要，不向历史运行目录写回；必要产物不完整时不返回 `symbol_metrics`。

`GET /runs/{run_id}?chart_payload=summary` 的响应扩展可选字段：

```json
{
  "symbol_metrics": [
    {
      "symbol": "600519.SH",
      "total_return": 0.124,
      "annual_return": 0.152,
      "max_drawdown": -0.062,
      "sharpe": 1.51,
      "win_rate": 0.625,
      "trade_count": 8
    }
  ]
}
```

现有 `chart_symbol=<symbol>` 查询保持不变，作为展开行的延迟图表数据来源。

## 前端交互与布局

在运行详情页标题区的现有组合 `MetricsCard` 下方，仅为 Graph 回测加入“各标的实际交易绩效”区块：

- 顶部总体指标继续沿用现有指标卡和本地化格式化函数，并扩展新增指标名称及格式。
- 下方为语义化表格，列含 symbol、总收益、年化收益、最大回撤、夏普、胜率、盈亏比、交易次数；窄屏时该表横向滚动。
- 列标题是可访问的按钮，显示当前排序方向；点击某一行或其展开按钮，在该行下方打开/关闭详情。
- 展开行以该 symbol 调用已有 `api.getRun(runId, { chart_symbol: symbol })`，复用 `CandlestickChart`、交易标记、指标序列与 `GraphSignalPanel`。已加载的 symbol 缓存在本页，重复展开不重复请求。
- 图表请求失败或该 symbol 无价格数据时，展开行显示局部提示，不影响指标表和其他 symbol 的操作。
- 当没有单标的指标时不渲染整个区块，以保持旧报告和非 Graph 报告现有行为。

## 文件职责

- 后端新增一个聚焦的指标计算模块，负责组合和单标的账本、指标与 CSV 的读写转换。
- Graph runner 调用该模块生成指标；产物写入层只负责原子发布 `metrics.csv` 与 `symbol_metrics.csv`。
- 运行详情聚合服务读取或回退计算单标的摘要，并把它加入现有响应。
- 前端新增独立的 `SymbolMetricsTable` 组件，负责排序、展开状态、按需图表缓存；`RunDetail` 只负责在 Graph 运行时挂载它。
- 指标标签、百分比、比率和整数格式全部集中在已有 `formatters.ts`，避免组件自行格式化。

## 错误处理与兼容性

- 非数值、无限值和没有有效样本的指标从响应和 CSV 行中排除。
- 成交缺少相应价格、卖出无法配对、或 OHLCV 无法构建日净值时，该 symbol 不生成依赖该数据的指标，但仍保留可由完整成交计算的交易计数等字段。
- 新字段均可选，旧的前端和旧的回测报告不因缺少 `symbol_metrics` 失败。
- 产物发布继续使用当前 staging 目录的原子替换流程，任一写入失败不留下部分发布的指标文件。

## 测试与验收

- 后端单元测试：固定成交和价格样本的组合/单标的收益、回撤、夏普、胜率、盈亏比、空样本和无亏损样本。
- 后端产物测试：验证新运行同时发布 `metrics.csv` 和 `symbol_metrics.csv`，并验证历史运行读取时的即时回退。
- API 测试：Graph 运行详情包含单标的摘要，非 Graph 运行没有该字段，`chart_symbol` 延迟载荷不变。
- 前端测试：Graph 详情显示指标表；表头排序正确；展开行请求对应 symbol 一次并渲染加载/无数据状态；非 Graph 详情不显示该区块。

验收时，选择任一含完整产物的 StockPred 报告，可先看到总体指标，再按任意指标排序单标的表；展开某行后可看到该 symbol 的既有 K 线和诊断图，且不会预取其他标的图表。
