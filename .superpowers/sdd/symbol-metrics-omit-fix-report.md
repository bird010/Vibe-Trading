# Symbol Metrics 字段省略修复报告

## 根因

`RunResponse` 的 `artifacts` 使用 `default_factory`。构造响应模型后再向其中追加工件不会将该字段标记为显式设置。全局使用 `exclude_unset=True` 会同时省略 `symbol_metrics` 和这些既有字段，改变了历史详情 API 的响应契约。

## 修复

- 恢复完整顶层序列化，保留 `artifacts` 等默认或后填充字段。
- 只在最终 payload 中，当 `symbol_metrics` 为 `None` 时移除该键。
- 对已存在的部分 Graph `metrics`，保留未设置指标字段不输出的既有语义。
- Graph 已发布但为空的 `symbol_metrics.csv` 会设置并返回 `symbol_metrics: []`。

## 验证

使用 `E:\anaconda3\envs\VibeTrading\python.exe`：

```text
pytest agent/tests/stockpred/test_run_response.py agent/tests/stockpred/test_api.py -q --basetemp tmp\\pytest\\symbol_metrics_omit_api
8 passed, 5 warnings
```

警告为既有 FastAPI 生命周期弃用提示及 TestClient 弃用提示。