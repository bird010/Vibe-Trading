# Task 3 实现报告

日期：2026-08-13

## 范围

- 修改 `agent/backtest/fund_rotation/runner.py`
- 新增 `agent/tests/fund_rotation/test_runner_native_execution.py`
- 新增测试专用 helper：`agent/tests/fund_rotation/conftest.py`
- 更新 Runner 既有测试夹具：
  - `agent/tests/fund_rotation/test_runner.py`
  - `agent/tests/fund_rotation/test_runner_contract_integration.py`
  - `agent/tests/fund_rotation/test_baseline_runner_parity.py`
  - `agent/tests/fund_rotation/test_correlation_representative_gates.py`
  - `agent/tests/fund_rotation/test_correlation_representative_integration.py`
  - `agent/tests/fund_rotation/test_signal_portfolio_risk_integration.py`
- 未修改 `forward_validation.py`
- 未修改生产 Shadow 文件
- 未修改 Task 2 native engine 文件

## TDD RED

先新增 Runner native wiring focused tests 后运行：

```powershell
pytest -q -p no:cacheprovider tests/fund_rotation/test_runner_native_execution.py
```

实际输出：

```text
FFFFF                                                                    [100%]
================================== FAILURES ===================================
______ test_spy_native_engine_is_called_once_and_its_result_is_returned _______
E       TypeError: FundRotationBacktestRunner.__init__() got an unexpected keyword argument 'execution_engine'

_ test_runner_native_formal_path_does_not_touch_legacy_loop_or_pipeline_adapter _
E       TypeError: FundRotationBacktestRunner.__init__() got an unexpected keyword argument 'execution_engine'

____ test_missing_explicit_pit_rule_inputs_fail_closed_without_engine_call ____
E       TypeError: FundRotationBacktestRunner.__init__() got an unexpected keyword argument 'execution_engine'

__________ test_diagnostics_are_computed_directly_from_native_ledger __________
E       TypeError: FundRotationBacktestRunner.__init__() got an unexpected keyword argument 'execution_engine'

___ test_exact_evaluation_calendar_and_cancellation_callback_are_preserved ____
E       TypeError: FundRotationBacktestRunner.__init__() got an unexpected keyword argument 'execution_engine'

5 failed in 1.33s
```

失败原因符合预期：Runner 还没有 native engine 注入边界，formal path 仍是旧 wiring。

## GREEN 与实现摘要

实现后 focused tests：

```powershell
pytest -q -p no:cacheprovider tests/fund_rotation/test_runner_native_execution.py
```

实际输出：

```text
.....                                                                    [100%]
5 passed in 1.03s
```

主要实现：

- `FundRotationBacktestRunner.__init__` 增加：
  - `execution_engine`
  - `market_rule_resolver`
  - `market_rule_instruments`
  - `market_rule_mode`
- 默认 engine 为 `FundRotationExecutionEngine()`。
- Runner 仍先完成策略 session、决策验证和 PIT universe evidence。
- formal execution path 改为从 sealed `targets_map`、exact `evaluation_dates`、pinned market frames、execution config、snapshot identity、knowledge cutoff、PIT rule resolver/mode/instruments 和 run id 构造 `NativeExecutionRequest`。
- Runner formal path 只调用一次 `execution_engine.execute(...)`，并把 cancellation callback 透传给 engine。
- `executed_equity`、`trade_events`、`orders`、`positions_history` 全部来自 `NativeExecutionResult`。
- `_formal_execution_diagnostics` 直接调用 `compute_execution_diagnostics_v2(result.ledger, ...)`，不再把 `PipelineResult` 或 `legacy_result` 作为 formal metrics 来源。
- diagnostics 增加 `execution_identity`，包含 `run_id`、snapshot version/fingerprint、rule mode、rule versions 和 source record ids。
- 缺失显式 PIT market rule resolver 或 instrument mapping 时，Runner 返回结构化失败：
  - `status=FAILED`
  - `error_code=EXECUTION_RULES_UNAVAILABLE`

## 指定回归

命令：

```powershell
pytest -q -p no:cacheprovider tests/fund_rotation/test_runner_native_execution.py tests/fund_rotation/test_runner.py tests/fund_rotation/test_runner_contract_integration.py tests/fund_rotation/test_integrated_review_repairs.py
```

最终实际输出：

```text
.........................................                                [100%]
============================== warnings summary ===============================
agent/tests/fund_rotation/test_integrated_review_repairs.py::TestConfigurationBoundaries::test_from_legacy_accepts_object_and_ignores_execution_fields
  E:\code\stock\Vibe-Trading\agent\tests\fund_rotation\test_integrated_review_repairs.py:98: PydanticDeprecatedSince211: Accessing the 'model_fields' attribute on the instance is deprecated. Instead, you should access this attribute from the model class. Deprecated in Pydantic V2.11 to be removed in V3.0.
    assert "initial_capital" not in converted.model_fields

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
41 passed, 1 warning in 2.18s
```

## Native engine 回归

命令：

```powershell
pytest -q -p no:cacheprovider tests/fund_rotation/test_native_execution.py tests/fund_rotation/test_execution_module.py tests/fund_rotation/test_execution_review_fixes.py tests/fund_rotation/test_executor.py
```

实际输出：

```text
.....................................                                    [100%]
37 passed in 3.37s
```

## 额外 Runner/parity 探测

命令：

```powershell
pytest -q -p no:cacheprovider tests/fund_rotation/test_baseline_runner_parity.py tests/fund_rotation/test_correlation_representative_integration.py tests/fund_rotation/test_signal_portfolio_risk_integration.py tests/fund_rotation/test_correlation_representative_gates.py
```

实际输出：

```text
..........sss...............                                             [100%]
25 passed, 3 skipped in 35.29s
```

结论：baseline/parity 和相关 Runner 集成测试现在通过显式测试 fixture 注入 PIT rule resolver/instruments。`run_signal_pipeline` 的测试通过 patch 内部 Runner 构造完成显式注入；未修改生产 `pipeline.py`，也未加入任何静态规则 fallback。

## Diff 检查

命令：

```powershell
git diff --check
```

实际输出：

```text
warning: in the working copy of 'agent/backtest/fund_rotation/runner.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'agent/tests/fund_rotation/conftest.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'agent/tests/fund_rotation/test_baseline_runner_parity.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'agent/tests/fund_rotation/test_correlation_representative_gates.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'agent/tests/fund_rotation/test_correlation_representative_integration.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'agent/tests/fund_rotation/test_runner.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'agent/tests/fund_rotation/test_runner_contract_integration.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'agent/tests/fund_rotation/test_signal_portfolio_risk_integration.py', LF will be replaced by CRLF the next time Git touches it
```

说明：仅 Git 行尾提示，无 whitespace error。

## 残余风险

- 生产 `run_signal_pipeline` 仍没有真实 PIT market rule source wiring；本次只在测试 fixture 中显式注入，未为生产 legacy adapter 增加 fallback。
- Native engine 的 rule provenance 当前主要通过 `orders`/`trade_events` 进入 Runner diagnostics 的 `execution_identity`；如果后续 Shadow/OOS 需要直接从 `ExecutionLedger` dataclass 读取 rule provenance，还需要扩展 ledger schema。
- Runner 使用单一 `knowledge_cutoff` 传入当前 Task 2 的 `NativeExecutionRequest` API；若未来要求每个 target/decision 独立 cutoff，需要扩展 native request contract。
