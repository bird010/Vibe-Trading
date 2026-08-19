# StockPred Graph 黄金工件

本目录只保存用于测试的最小夹具。完整黄金工件体积较大，不纳入版本库；它们由冻结的 StockPred Graph oracle 离线生成。

- Oracle 提交：`91e7f5bbb52f03f64ea52b50d0dfe5f2a076be6e`
- 固定参数：`top_n=50`、`eval_step=5`、`forward_days=5`、`n_workers=1`
- 完整产物：`manifest.json`、`details.parquet`、`selected.csv`、`trades.csv`、`equity.csv`、`metrics.json`

标准验证窗口：

| 名称 | 开始日期 | 结束日期 |
| --- | --- | --- |
| normal | 2025-01-02 | 2025-03-31 |
| pit-boundary | 2024-03-01 | 2024-05-31 |
| execution-edge | 2024-09-02 | 2024-11-29 |

在 Vibe-Trading 根目录运行：

```powershell
python tools/migration/export_stockpred_graph_golden.py `
  --stockpred-root ..\StockPred `
  --start 2025-01-02 `
  --end 2025-03-31 `
  --out tmp\golden\normal
```

导出器拒绝覆盖已有目录，并在 `manifest.json` 中记录 oracle 提交和参数。若运行环境禁止子进程写入工作区，可把 `--out` 指向系统临时目录；产物内容不受输出位置影响。
