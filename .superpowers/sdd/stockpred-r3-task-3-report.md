# StockPred Cohort R3 Task 3 验证报告

Base：`a12a504d554a0b98260aa6cd67ef5f65919770b9`

## 实现范围

- 图表覆盖 eligibility 过滤后的全部 `signal_codes`；`selected_codes` 仍只用于下单目标。
- 图表发布窗口采用引擎的 `data_start/data_end`，覆盖 ADV 回看与最长退出延迟；空信号运行也发布合法的空 chart manifest。
- 新增 symbols 与 period-breakdown API，并将 Cohort API 内的 NaN/Inf 递归序列化为 `null`。
- Cohort 前端支持个股 tab、延迟加载 symbols/chart、K 线字段映射、订单标记、cohort 筛选和年/季稳定性表；切换 `runId` 会取消旧请求。
- `RunDetail` 明确分流 cohort、legacy 和未知 schema；legacy 报告内容现在是 `LegacyStockPredReport` 的真实 children。`symbol_metrics` 保持 `isGraphRun` 条件，因为其语义为 Graph 专属且既有回归要求非 Graph run 不显示该表。

## 版本化产物完整性

- 发布器在 `chart_bundle_manifest.json` 写入 `files` 索引：每个非 manifest 文件都记录相对路径、SHA-256 与字节数。
- 新式 64 位 manifest SHA pointer 仅先读取并校验 manifest；解析 `files` 索引后，API 对 metrics、returns、quality、period、chart 和 orders 逐个按需读取、校验哈希和字节数，再解析。不会在新路径全量读取 parquet。
- 旧式 32 位兼容 pointer 仅在 `manifest_sha256 == version_id` 时使用；会对完整版本目录按发布器规则重算内容 hash，匹配后才接受。
- manifest、索引文件和旧兼容快照都经 `Path.resolve()/relative_to()` 目录边界校验；manifest 或旧快照中的符号链接指向目录外会拒绝。
- 按需读取期间出现的哈希不匹配会由 Cohort API 稳定返回 404，不会继续解析未验证的磁盘内容。

## TDD 证据

审查修复先执行以下 RED 测试：

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_artifact_resolver.py agent/tests/stockpred/test_api.py -q -p no:cacheprovider --basetemp=C:\Users\LK\AppData\Local\Temp\pytest_stockpred_r3_task3_review_red
```

结果为 `4 failed, 21 passed`：合法旧 32 位 pointer 被拒绝；篡改后的版本内容没有被 fail-closed；metrics 篡改会落入 JSON 解析异常；returns 篡改仍返回 200。

随后新增的空运行 `files` 索引断言先失败（缺少 `files`），真实 chart/orders 篡改用例也先失败（测试 fixture 尚不支持有效图表），再分别实现索引与按需校验。

## GREEN 与回归证据

```powershell
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/stockpred/test_chart_bundle.py agent/tests/stockpred/test_artifact_resolver.py agent/tests/stockpred/test_cohort_artifacts.py agent/tests/stockpred/test_api.py -q -p no:cacheprovider --basetemp=C:\Users\LK\AppData\Local\Temp\pytest_stockpred_r3_task3_final
# 43 passed in 1.53s

Set-Location frontend
npm run test:run -- --reporter=dot src/components/stockpred/__tests__/CohortStockPredReport.test.tsx src/pages/__tests__/RunDetail.test.tsx
# 15 passed
npx tsc --noEmit -p tsconfig.json --pretty false
# exit 0

Set-Location ..
ruff check --no-cache agent/backtest/stockpred/cohort/artifacts.py agent/backtest/stockpred/cohort/chart_bundle.py agent/src/stockpred/artifact_resolver.py agent/src/api/stockpred_routes.py agent/tests/stockpred/test_artifact_resolver.py agent/tests/stockpred/test_cohort_artifacts.py agent/tests/stockpred/test_api.py
# All checks passed!
```

完整前端测试此前的结果为 `261 passed, 1 failed`。唯一失败是未触及的 `src/components/charts/__tests__/GraphSignalPanel.test.tsx`：测试期望 yAxis 名称为 `Score`，实际为 i18n key `stockPred.score`。

## 工作区说明

未暂存、删除或修改外部生成的 `review_c*.txt`、`review_diff.txt`、`tmp_*.diff`。由于工作区 pytest 临时目录 ACL 限制，后端验证使用提升权限及 `C:\Users\LK\AppData\Local\Temp` 的独立 `basetemp`；未修改或删除原目录。
