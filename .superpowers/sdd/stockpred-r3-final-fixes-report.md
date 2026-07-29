# StockPred R3 最终修复报告

Base：`8df89def603434f00904c07fb98ead2ae8dcde44`

## 范围

1. Chart bundle 对非空请求代码实行 fail-closed；缺少扩展窗口 OHLCV 时抛出稳定原因 `CHART_BUNDLE_INCOMPLETE`，发布 staging 清理且旧 pointer 不变。
2. `cohort_orders.csv` 记录所有 entry/exit `ExecutionEvent`，包括 FILLED、PARTIAL、REJECTED；保留订单、数量、金额、状态、原因、五项费用与总费用。前端只为实际成交量大于零的订单画 marker，并兼容旧 `quantity` 字段。
3. Ledger 在写入前校验执行事件数值、金额、数量和费用不变量；非法事件只标记 `FAILED_EXECUTION`，不改变资金/持仓/费用。失败后的 ledger 不再接收后续状态变更，runner 输出 null returns。

## TDD 记录

| 项目 | RED | GREEN |
| --- | --- | --- |
| Chart 完整性与旧 pointer | `test_missing_requested_code_fails_closed`、`test_incomplete_chart_bundle_cleans_staging_and_keeps_old_pointer`：2 failed，均为未抛出异常 | 2 passed |
| 订单审计与数量语义 | entry partial、延迟 exit、orders artifact：3 failed（数量关系错误、CSV 缺少 `order_id`） | 3 passed |
| 拒单 marker | Cohort 前端测试：1 failed（REJECTED SELL 被显示） | 5 passed |
| Ledger/runner 非法事件 | 8 failed（ledger 保持 HOLDING/UNLIQUIDATED） | 8 passed |
| Exit 拒单与失败粘滞 | 2 failed（跌停日未产生事件、FAILED ledger 被后续事件修改） | 2 passed |

所有 Python pytest 命令均使用提升权限及新的系统 `--basetemp`，避免已有 Windows 临时目录 ACL 干扰。

## 最终验证

- 相关后端套件：`103 passed in 2.92s`
  - chart bundle、artifacts、execution policy、cohort ledger、cohort engine、metrics、API。
- 前端 Cohort/Candlestick 测试：`7 passed`。
- TypeScript：`npx tsc --noEmit` 通过。
- Ruff：本任务 Python 文件 `All checks passed!`。
- `git diff --check` 通过。

## 自审

- Rejected exit 会写入 orders artifact，但零成交不会写入 benchmark exit events，避免改变清算基准窗口。
- 空 `chart_codes` 继续生成合法空 manifest。
- 未触碰或暂存 `review_c*.txt`、`review_diff.txt`、`tmp_*.diff`。
