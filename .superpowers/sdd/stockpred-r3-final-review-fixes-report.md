# StockPred Cohort R3 最终审查修复报告

状态：完成。Windows `tmp_path` ACL 阻止本进程运行依赖该 fixture 的 engine/artifact 整合 selector，主流程已代跑全部关键 selector 并确认 GREEN。

## 第 1 项：历史退市 as-of 资格

- RED：`E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent\tests\stockpred\test_cohort_final_review.py::test_historically_listed_currently_delisted_stock_remains_eligible_before_delisting -q`
  - 预期失败，实际为 `eligible_codes == []`，证明代码将当前 `list_status == D` 直接排除。
- GREEN：同 selector 加 `-p no:cacheprovider`，`1 passed`。
- 修改：`eligibility.py` 对 `D` 状态要求有效退市日，按 `list_date <= eval_date < delist_date` 处理；`D` 且退市日不可验证时 fail-closed。

## 第 2 项：基准复权价 fail-closed

- RED：`...pytest agent\tests\stockpred\test_cohort_final_review.py::test_benchmark_without_adjusted_open_fails_closed -q -p no:cacheprovider`
  - 失败：仅原始 `open` 的输入返回 `0.1`。
- GREEN：`...pytest agent\tests\stockpred\test_cohort_final_review.py::test_benchmark_without_adjusted_open_fails_closed agent\tests\stockpred\test_cohort_benchmark.py -q -p no:cacheprovider`
  - `9 passed`。
- 修改：benchmark 只接受有限、正的 `adj_open`，任何必需数据不足返回 `BenchmarkResult(benchmark_return=None)`；引擎将任一基准不足记录为 `FAILED_DATA`、收益保持 null。

## 第 3 项：raw label coverage

- RED：`...pytest agent\tests\stockpred\test_cohort_final_review.py::test_raw_label_without_adjusted_open_fails_closed -q -p no:cacheprovider`
  - 失败：仅原始 `open` 的输入给出 coverage `1.0` 和收益 `0.1`。
- GREEN：`...pytest agent\tests\stockpred\test_cohort_final_review.py::test_raw_label_without_adjusted_open_fails_closed agent\tests\stockpred\test_cohort_metrics.py -q -p no:cacheprovider`
  - `14 passed`。
- 修改：raw label 只使用 `adj_open`；缺列时 coverage 为 0、收益为 null、状态为 `insufficient_data`。新增 config `min_raw_label_coverage=1.0` 并写入 protocol config；引擎低于阈值时记录 `FAILED_DATA`。

## 第 4 项：事实表日期和 data_quality

- RED（由主流程在可访问 tmp_path 环境执行）：`agent/tests/stockpred/test_cohort_final_review.py::test_cohort_csv_preserves_evaluation_date_and_data_quality`，明确失败于 CSV 缺 `evaluation_date` 和 `data_quality` 列。
- 已实施最小修改：`CohortResult` 有 `evaluation_date`，CSV 保留以稳定 JSON 序列化的 `data_quality`。
- GREEN（主流程隔离 `basetemp`）：CSV selector 已通过；失败路径均传播 `evaluation_date`，空 CSV 也采用完整 `CohortResult` schema。

## 第 5 项：陈旧估值

- RED：`...pytest agent\tests\stockpred\test_cohort_final_review.py::test_cohort_result_exposes_stale_valuation_audit_fields -q -p no:cacheprovider`，失败于缺审计字段。
- GREEN：同 selector，`1 passed`。
- GREEN：陈旧比例 4% 的定向 selector 为 `1 passed`。
- 已添加 `uses_stale_valuation`、`max_stale_days` 契约字段；terminal valuation 传播最大陈旧天数；聚合在比例大于 2% 时加入 `max_stale_valuation_ratio`。

## 第 6 项：legacy 路由

- RED（主流程隔离 basetemp）：legacy StockPred context 实际为 `None`，预期 `legacy_portfolio_like_v1`。
- GREEN（主流程隔离 basetemp）：`1 passed`。
- `load_run_context` 仅对明确 `strategy_type=stockpred_strategy` 且存在 `artifacts/` 的无 schema run 推断 legacy；非 StockPred run 保持无 schema。
- 前端现有未知显式 schema fail-safe 未改。

## 第 7 项：top_n 和空 target

- RED：`...pytest agent\tests\stockpred\test_cohort_final_review.py::test_cohort_config_rejects_zero_top_n -q -p no:cacheprovider`，失败：`top_n=0` 未抛异常。
- GREEN：同 selector，`1 passed`。
- 已实施：`top_n=Field(default=50, ge=1)`。
- 已实施：防御性空 target 追加 `FAILED_DATA/empty_target` 事实行，并携带 evaluation_date。

## 全量验证与环境限制

- 后端可运行定向套件：`63 passed, 1 deselected`（benchmark、metrics、aggregation、contracts、final-review 非 tmp_path selector）。
- 前端 TypeScript：`npm exec tsc -- --noEmit` 通过。
- 前端 legacy/unsupported routing：`npm run test:run -- src/pages/__tests__/RunDetail.test.tsx`，`11 passed`；首次 sandbox 内 esbuild spawn EPERM，升权后通过。
- `git diff --check` 与 staged diff check 通过。
- AlphaFin 环境未安装 `ruff`（`No module named ruff`），因此本进程未获得 Ruff 静态检查证据。

## 收尾复审修复（主流程 117 passed、2 failed 后）

- 基准 RED/GREEN：`test_empty_exits_with_valid_index_returns_zero` 从空 exit event 返回 `None` 的 RED，修复后 `1 passed`。有效 index `adj_open` + 空现金流现在返回 `0.0`；缺 index/缺 `adj_open` 仍返回 `None`。引擎在 `FAILED_EXECUTION` 后立即产出 null 指标结果，避免后续基准逻辑覆盖状态。
- 集成样本：将超出固定期限的 `20250125` 改为 `20250120`，并将 benchmark helper 的返回契约改为 `float | None`；正常路径不再把 `None` 传给 `compute_cohort_result` 计算。
- raw/stale 审计：失败 cohort 保留实际 `raw_label_coverage/raw_label_status`；基准失败同时保留已计算的 raw 与 stale 审计字段。horizon/terminal stale 传播 engine RED 后主流程确认 GREEN。
- 事实表：CSV 值测试验证 evaluation date 与 `json.loads(data_quality)["reason"]`；空 CSV 采用完整 `CohortResult` schema。主流程确认空 schema GREEN。
- 资格、协议与空 target：补齐评估日退市和退市日缺失 fail-closed 测试；protocol `quality_gate` 显式写入 `max_stale_valuation_ratio=0.02`；空 target engine RED 后主流程确认追加 `FAILED_DATA/empty_target/evaluation_date` 的 GREEN。
- protocol gate 的行为 RED（实际 `{}`）与 GREEN（`1 passed`）均由主流程隔离 basetemp 确认。
- 主流程关键 selector 最终为 `4 passed`（empty schema、empty target、stale propagation、malformed execution）。本进程复跑无 tmp_path 相关套件为 `69 passed, 1 deselected`；最后 TypeScript `npm exec tsc -- --noEmit` 通过，`git diff --check` 通过。
- 主流程完整相关套件随后为 `125 passed, 1 failed`；唯一失败是 pandas 将 CSV 中的纯数字日期推断为 `np.int64`，测试已改为将读取值转为 `str` 后核对，未修改生产序列化。
- 最后复核波：raw label 的所有早退在零阈值下仍为 `insufficient_data`；None、`pd.NA` 和非数值 `adj_open` 以 fail-closed 处理而不抛转换异常（本进程 RED 后 GREEN，4 passed）。主流程确认 terminal stale=999 的 `FAILED_DATA` 与 malformed exit 保留 stale 审计的 engine RED/GREEN（2 passed）。本进程最终非 tmp_path 定向为 `34 passed, 1 deselected`，diff check 通过。

## 第 8 节：生产总回报基准正向路径

- RED：主流程确认 H00300.CSI gateway 输出缺 `adj_open`，两处默认 benchmark 仍是 `000300.SH`，production-shaped cohort index frame 进入 `FAILED_DATA`。
- GREEN 实现：仅 `StockPredDataGateway.index_daily("H00300.CSI", ...)` 在 gateway 视图中由该总回报指数自身的 `open` 显式生成 `adj_open`；原始 `fact_index_daily` schema 未变，普通价格指数（如 000300.SH）不生成该列，也没有代码映射。
- `CohortBacktestConfig`、`StrategyBacktestConfig` 默认 benchmark 改为 `H00300.CSI`，故 protocol config 和 strategy-to-cohort 运行上下文采用实际总回报代码。
- GREEN（主流程隔离 `basetemp`）：gateway 总回报视图、普通价格指数保护、两处默认值及 production-shaped cohort runner 共 `5 passed`；覆盖 11 个 gateway/strategy/cohort/run_analysis 文件的完整相关回归为 `153 passed in 8.11s`。
- 本进程补充验证：两处默认值 selector 为 `2 passed`，非 `tmp_path` 相关后端回归为 `81 passed, 1 deselected`。未使用隔离 `basetemp` 的扩展套件出现 `55 passed, 45 errors`，错误均为既有 Windows `tmp_path` ACL（`PermissionError [WinError 5] ... pytest-of-LK`）在 setup 阶段阻断，未发现行为断言失败。
- `git diff --check` 通过；本节未修改事实表 schema、未对普通价格指数伪造复权字段。

## 第 9 节：总回报基准生产链路闭环

- RED：`H00300.CSI` 未被 Tushare `_is_index` 识别，日线误走股票 `daily` 且会尝试 `daily_basic`；两个 benchmark helper 默认仍为 `000300.SH`；engine 调用 helper 时未传 `config.benchmark_code`。主流程确认定向结果为 `5 failed, 13 passed`，失败原因均与上述缺口一致。
- 真实链路测试不再 monkeypatch gateway `_read`：测试 fixture 写入 Lance `fact_index_daily`，分别验证 H00300.CSI 暴露 `open + adj_open`、000300.SH 保持无 `adj_open`；runner 的 `index_daily` 委托真实 `StockPredDataGateway`，覆盖 Lance → Gateway → Runner。
- GREEN 实现：Tushare 指数识别仅精确新增可验证代码 `H00300.CSI`，因此日线走 `index_daily` 且跳过股票基本面 enrichment；普通股票、ETF 与现有价格指数语义不变。两个 benchmark helper 默认改为 `H00300.CSI`，engine 对两者均显式传入 `config.benchmark_code`。
- 本进程聚焦 GREEN 为 `5 passed`；Tushare loader 与 cohort benchmark 全文件回归为 `67 passed, 4 skipped`。
- 主流程隔离 `basetemp` 的完整 GREEN 回归覆盖 Tushare loader、gateway、strategy、cohort 与 run_analysis，为 `213 passed, 4 skipped in 9.25s`；其中真实 Lance → Gateway → Runner 与采集路由 selector 均通过。
