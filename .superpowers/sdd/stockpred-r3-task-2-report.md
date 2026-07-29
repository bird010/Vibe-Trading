# StockPred Cohort R3 Task 2 报告

## 范围与结果

- Eligibility 使用候选信号股票的信号日原始行情、复权因子、上市/退市状态、ST 和市场日历；无法确认的数据按 fail-closed 处理。
- 所有计划评估日均产生 cohort 记录：合法的全拒绝为可审计空 cohort，数据失败为 `FAILED_DATA`。
- `FAILED_DATA` / `FAILED_EXECUTION` 的收益、超额收益和基准字段均为 `None`，不再伪造零收益。
- 截断 horizon 直接标记 `FAILED_DATA`，不缩短持有期。
- 执行事件保留 cohort、requested value、数量、剩余数量、状态、原因和费用；账本拒绝跨 cohort、错方向、重复 event ID 和 oversell，并标记 `FAILED_EXECUTION`。
- in-process Alpha batch 在 `evaluation_engine="cohort"` 时与 process worker 一样调用 `CohortRunner`；`req.json` 的 worker 路径改用 `atomic_json`。
- PIT 未知依赖降级为 `snapshot_only`。ADV 接收市场交易日历：缺少市场日的股票行是质量失败，只有存在的 `vol=0/amount=0` 行才作为确认停牌的零值。

## TDD 证据

先写并运行的 RED：

1. 资格检查新增 adjustment factors / market calendar 参数前，suspended 和 adjustment coverage 测试因缺少参数失败。
2. `ExecutionEvent.requested_value`、`ExecutionPolicy.cohort_id`、跨 cohort / oversell 失败语义、PIT unknown 降级，均在初始测试中按预期失败。
3. `test_in_process_cohort_request_uses_cohort_runner` 在旧 in-process portfolio 路径返回 legacy metrics，按预期失败。
4. ADV 市场日历缺口测试在 `trade_dates` 参数不存在时按预期失败。
5. exit event requested value 测试在事件值为 `0.0` 时按预期失败。

首次合并 RED 命令还受到既有 `tmp` ACL 限制，导致依赖 `tmp_path` 的 10 项测试无法 setup；随后以受控 `--basetemp=tmp/pytest_stockpred_r3_task2_*` 并获授权运行，未发现基线失败。

## 最终验证

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_cohort_engine.py agent/tests/stockpred/test_cohort_ledger.py agent/tests/stockpred/test_batch_screening.py agent/tests/stockpred/test_pit_assurance.py agent/tests/stockpred/test_execution_adv.py agent/tests/stockpred/test_execution_policy.py agent/tests/stockpred/test_cohort_metrics.py agent/tests/stockpred/test_cohort_contracts.py agent/tests/stockpred/test_cohort_integration.py -q --basetemp=tmp/pytest_stockpred_r3_task2_final2
```

结果：`90 passed, 18 warnings in 3.43s`。warnings 为既有 pandas FutureWarning（batch screening / graph performance），无失败。

```powershell
ruff check --no-cache <本任务修改文件>
```

结果：`All checks passed!`

## 自审

- 未修改 Task3 前端或 Chart 文件。
- 所有失败 cohort 都进入 aggregation 的 total count；aggregation 只以有效 cohort 计算收益统计。
- 未为测试添加生产专用 API；batch route test 仅替换 runner 的昂贵执行以观察 coordinator 的可见 routing 结果。
- 停牌判断只使用现有 `vol` 与 `amount` 的实际行；市场日历中的缺失行不补零。若未来需要更细的停牌证据，应先扩展数据 schema，而非推测字段。

## 提交

实现提交：`c2abade fix(stockpred): harden cohort eligibility and execution`。

## 审查修复（R3 复审）

新增 RED 命令（HEAD `00e2583`）：

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_empty_signal_date_is_auditable_failed_cohort agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_signal_evaluation_exception_is_failed_data_and_does_not_abort agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_exit_extension_truncation_is_failed_data agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_period_breakdown_excludes_failed_none_returns_but_counts_them agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_strategy_declared_unknown_dependency_downgrades_pit agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_missing_name_history_and_market_calendar_fail_closed agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_candidate_absent_from_universe_is_data_failure agent/tests/stockpred/test_cohort_ledger.py::test_cash_insufficient_entry_fails_execution agent/tests/stockpred/test_execution_adv.py::test_adv_zero_amount_with_nonzero_volume_is_quality_failure -q --basetemp=tmp/pytest_r3_review_red
```

结果：`9 failed`，分别证明空信号未记录、信号异常中断、延期被截断、None 收益崩溃、PIT 硬编码、Eligibility 非 fail-closed、账本仅 warning、ADV 不一致零成交未失败。

另一个 RED：`test_in_process_cohort_patches_req_json_atomically` 失败为 `KeyError: metric_schema_version`；共享 cohort config helper 在未实现前 import error。

GREEN：最终完整 Task 2 测试命令通过 `101 passed, 18 warnings`；warnings 均为既有 pandas FutureWarning。审查修复包括：全部计划日审计 cohort、延期期限失败、period None 过滤且保留 count、实际声明/未知 PIT 依赖降级、现金失败、统一 cohort config + snapshot digest、两路 atomic req.json、严格 eligibility、ADV zero/volume 校验及终端市场日历。

## Signal-day 原始行情复审修复

RED：

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_missing_or_nan_signal_day_raw_row_is_data_failure agent/tests/stockpred/test_cohort_engine.py::TestCohortRunner::test_missing_signal_day_raw_row_produces_failed_data_with_null_returns -q --basetemp=tmp/pytest_signal_raw_red
```

结果：`2 failed`。缺失 raw row 的 EligibilityResult 为 `NO_MARKET_DATA` 但 `data_failure=False`；runner 错误地产生 `LIQUIDATED` 空 cohort。

GREEN：同两项命令通过 `2 passed`。最终完整 Task 2 回归为 `103 passed, 18 warnings`，ruff 通过。缺失 raw row 或 `vol=NaN` 现在为数据失败；确认 `vol=0` 仍是合法停牌拒绝。若全局 name history 存在但没有该股票在信号日前的有效记录，也按无法证明的保守语义标为失败。
