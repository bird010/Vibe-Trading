# Task 2 实现报告：PIT U0/U1 身份快照与诊断

## 范围与结论

本次只实施 Task 2，没有修改 Task 3 以后内容，也没有修改 R39 策略逻辑。
实现复用既有 `PITFundMaster`、`UniverseResolver`、
`FundRotationPITUniverseAdapter` 和 Runner diagnostics 边界：默认 adapter
identity layer 仍为 U0；只有显式指定 `identity_layer="U1"` 才向 Runner
投影去重后的代表基金。

U1 的 canonical identity key 使用 underlying index、asset class、region、
currency、leveraged/inverse 和 share-class/feeder relationship。U0 沿用
`AS_WAS_KNOWN`、上市/退市半开区间、旧三层 exclusion 和旧 audit metrics；
U1 对 U0 做确定性排序、同 identity 选择最小 `ts_code`，身份缺失则
`PIT_INVALID` 且不纳入 U1。快照和映射使用 frozen dataclass 与只读 mapping，
hash 使用规范化 JSON + SHA-256。

## Git 状态

- base commit：`6710a4ac`（Task 1 review gate 记录）
- implementation head：`99f9df9f`（Task 2 最终 U1 一致性修复提交；最终 review 目标为该实现及本报告）
- Task 1 报告已在前一独立提交中完成，本 Task 未修改其内容。

## TDD 记录

| 阶段 | 命令/结果 | 退出码 |
|---|---|---:|
| RED：U0/U1 identity tests | 新 API 不存在，4 个测试因 `AttributeError` 失败 | 1 |
| RED：Runner identity propagation | identity fields 未透传，断言失败 | 1 |
| RED：新增 fail-closed 边界测试 | `3 failed, 17 passed` | 1 |
| RED：review P1 回归测试 | `2 failed, 1 error`（error 为系统临时目录 ACL） | 1 |
| RED：review P2/P3 回归测试 | `3 failed, 26 passed` | 1 |
| RED：最终 review cutoff/时区回归测试 | `2 failed, 34 passed` | 1 |
| RED：最终 review 历史质量/审计回归测试 | `2 failed, 36 passed` | 1 |
| RED：最终 review 质量聚合/list_date/manifest 回归测试 | `3 failed, 38 passed` | 1 |
| GREEN：PIT/Runner/universe focused | `74 passed` | 0 |
| GREEN：execution ledger/原生 runner 相关回归 | `30 passed` | 0 |
| 脚本默认诊断 | 生成 `unavailable` manifest/report，无伪造研究数字 | 0 |
| `git diff --check` | 通过；仅有 Git 的 LF/CRLF 提示 | 0 |

Focused 命令：

```text
$env:PYTHONDONTWRITEBYTECODE='1'; $env:PYTHONPATH='agent;agent/src';
E:\anaconda3\envs\AlphaFin\python.exe -m pytest -p no:cacheprovider
  agent/tests/fund_rotation/test_pit_identity_layers.py
  agent/tests/fund_rotation/test_pit_universe.py
  agent/tests/fund_rotation/test_runner_contract_integration.py
  agent/tests/fund_rotation/test_universe.py -q
结果：74 passed，退出码 0

扩展回归命令覆盖 `test_execution_ledger_v2.py`、`test_native_path_contracts.py`、
`test_runner_native_execution.py`，结果：30 passed，退出码 0；合并运行结果：104 passed。
```

## 回归与环境限制

按计划运行的完整命令为：

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation -q
```

该命令在收集阶段退出码为 1。设置 `PYTHONPATH=agent;agent/src` 后，路径问题
消除，但仍有 4 个既有测试因 AlphaFin 环境缺少 `defusedxml` 而无法收集：
`test_engine_migration.py`、`test_engine_parity.py`、`test_repair_round2.py`、
`test_review_contracts.py`。这不是本次改动引入的失败；PIT、universe、runner
相关回归已由上述 74 个 focused tests 覆盖并通过；扩展 execution ledger/Runner
相关回归为 `30 passed`，合计本轮直接验证 104 passed。

一次 `py_compile` 尝试退出码为 1，原因是既有 `__pycache__` ACL 拒绝写入；
通过 pytest 的无字节码导入执行完成了相同模块的语法/导入验证。

## 实验产物与 hash

脚本：
`experiments/fund_rotation_research_validity/pit_identity.py`

默认执行命令：

```text
E:\anaconda3\envs\AlphaFin\python.exe experiments/fund_rotation_research_validity/pit_identity.py
```

产物：

- `experiments/fund_rotation_research_validity/batch_1/manifest.json`
  - SHA-256：`bcd4f4fdf37adb94b2278d2ea269356d6ad6a8cd60f53b8b0c6b485ceaf20b31`
- `experiments/fund_rotation_research_validity/batch_1_report.md`
  - SHA-256：`41adfe29471ac540588652359beac452fed67842f113b9538757db4584100447`

当前仓库没有可验证的 PIT master、R39 冻结 manifest、调仓日期清单和决策日
可交易源，因此 manifest 的 snapshot 与 R39 三折成本/延迟实验均为
`unavailable`。脚本不会用零值替代缺失证据，也没有伪造 U0/U1 收益、成本、
momentum、cluster 或 T+1/T+2 结论。提供完整输入时，脚本会逐日期调用现有
resolver 生成 U0/U1 membership、identity mapping、identity hash、snapshot
fingerprint 和 coverage diagnostics。

计划中的 R40 Shadow 104 周真实前瞻证据仍未取得；本 Task 不启动、不声称资格，
该限制保持明确未解决。

## 修改文件与最小改动自评

本 Task 新增或修改：

- `agent/backtest/fund_rotation/pit_universe.py`
- `agent/backtest/fund_rotation/runner.py`
- `agent/tests/fund_rotation/test_pit_identity_layers.py`
- `agent/tests/fund_rotation/test_runner_contract_integration.py`
- `experiments/fund_rotation_research_validity/pit_identity.py`
- `experiments/fund_rotation_research_validity/batch_1/manifest.json`
- `experiments/fund_rotation_research_validity/batch_1_report.md`
- `.superpowers/sdd/2026-08-29-fund-rotation-research-validity-plan/task-02-report.md`

未修改 `agent/backtest/fund_rotation/universe.py`，因为现有静态 universe contract
不需要身份策略字段；未触碰 snapshot 数据读取、R39 strategy、公共执行账本或
平台级架构。每个新增字段/API 都服务于 Task 2 的身份快照或 diagnostics 传播。
自评为最小兼容扩展；实现层面未发现明显 P0/P1，但独立 reviewer 尚未完成，
因此不将本报告作为 reviewer PASS，也不推进 Task 3。

## 独立 review 状态

前两轮 review 的 6 个 P1 与最终 review 发现的 3 个 P1 已用 RED/GREEN 关闭；最终
review 的 2 个 P2 和 1 个 P3 也已用 RED/GREEN 关闭：证据
标志严格布尔解析、缺失/非法可交易数据 fail-closed、同 knowledge time 的 revision
不任意选择、重复 tradability 记录冲突确定性失败、非法生命周期日期（含逆序区间）
稳定 exclusion、tradability 缺失优先判为 `PIT_INVALID`、实验产物拒绝覆盖、完整
coverage 纳入 fingerprint、leverage 布尔值统一归一化、tradability 日期统一规范化；同时
消除了 U1 adapter 的重复 resolver 调用，并使混合缺失 identity 的 U1 输出为空且标记
`PIT_INVALID`；runner 统一传递决策日 `T15:00:00` cutoff，查询与源时间戳时区混用
则稳定返回 `PIT_INVALID`，历史 PIT 查询的质量状态会纳入 run status，U1 保留 U0
非 eligible 的排除理由，历史与 benchmark diagnostics 按日期保留，缺失 list_date 与
逐日 PIT_INVALID 会进入正式质量/manifest 状态；native execution rule 也使用决策日
`T15:00:00` cutoff。当前等待最终独立复审。

- `luna_model = gpt-5.6-luna`
- `luna_reasoning = high`
- `luna_review_result = PASS`
- `p0_count = 0`
- `p1_count = 0`
- `p2_count = 0`
- `p3_count = 0`
- `decision = PROCEED_TO_TASK_3`
