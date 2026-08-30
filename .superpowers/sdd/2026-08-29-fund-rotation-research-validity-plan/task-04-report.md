# Task 4 实现报告：冻结 R40 并启动 Shadow A

## 结论

已冻结 `ai_rotation_r40_single_name_ceiling`，固定 single-name ceiling=`0.5`，
并创建 Shadow A 的冻结版本、qualification policy、初始 evidence/assessment、
连续账户状态、指标/漂移/事件文件和 shadow deployment manifest。Shadow 状态为
`STARTED`，资格状态为 `INSUFFICIENT_FORWARD_EVIDENCE`；启动不等于资格通过，
没有声称 104 周资格或部署晋级。

## 修改文件与最小改动

- `experiments/fund_rotation_research_validity/start_r40_shadow.py`
- `experiments/fund_rotation_research_validity/shadow_a/` 下冻结/资格/账户/append-only artifact
- `experiments/fund_rotation_research_validity/shadow_a_report.md`
- `agent/tests/fund_rotation/test_r40_shadow_start.py`

未修改 `forward_validation.py`、`production_adapters.py`、R39/R40 策略逻辑、公共
execution ledger、contracts、平台架构或历史记录；直接复用既有
`FrozenStrategyVersion`、`ShadowDecisionService`、`ShadowExecutionService`、
`ShadowRunScheduler` 和 `shadow_*` artifact contracts。启动脚本只做最小 wiring，且裸运行时
仅 bootstrap 项目内 `agent` import path。

## TDD 与验证

- RED：启动入口不存在时按预期失败。
- Task 4 focused：`34 passed`，退出码 0；其中既有 forward-validation 28 项和新增 Shadow A 6 项。
- Shadow A 默认启动脚本：退出码 0；第二次不可变产物复跑退出码 0 并保持幂等。
- `git diff --check`：通过。

## 产物与 hash

- `experiments/fund_rotation_research_validity/shadow_a/shadow_manifest.json`
  - SHA-256：`3c6bf73e027aa8f34e096ae3078c77cca6e6e3cfb6f96fd64b9549d4d191382b`
- `experiments/fund_rotation_research_validity/shadow_a_report.md`
  - SHA-256：`1619ada422ddd95dbaf7aaef71ddb841c84c2132ec92954c21f1c640182f52a6`
- `experiments/fund_rotation_research_validity/shadow_a/shadow_integrity.json`
  - SHA-256：`fef9617bb11f6a50e7b9b2c221088f913f125ad3ee52d9ddfe9bb0e77a3bc0d7`

冻结 strategy version 为 `sv-9525ec8754d100c0`；R40 源文件 SHA-256 为
`12ec4e737be6438260b0b0f9c7ce74525d5514c7fdd895cf9e7f65edf61a2655`，同时绑定在
frozen manifest config、implementation identity/hash 和 Shadow manifest。复跑会校验当前
源文件 hash、请求的 U1/R39 输入身份、全部非 manifest artifact hash 以及报告中绑定的
Shadow manifest hash；默认产物还由启动入口内的提交级 manifest anchor 复核。

manifest 内记录了每个冻结/append-only artifact 的 SHA-256。由于当前没有真实 U1
identity、R39 control identity、forward 服务和行情输入，首个 cycle 不生成虚假的
decision/order/fill/收益数据；初始连续账户的 ideal/executable NAV 均保留为真实初始
状态，事件文件为空。104 周证据、事件后 1 周/1 月/3 月收益、尾部损失、worst week、
MDD、换手和延迟均为 `unavailable`。

## 独立 review 状态

独立 `gpt-5.6-luna` 高推理 review 已通过；P0/P1/P2/P3 均为 0，允许推进 Task 5。

- `luna_model = gpt-5.6-luna`
- `luna_reasoning = high`
- `luna_review_result = PASS`
- `p0_count = 0`
- `p1_count = 0`
- `p2_count = 0`
- `p3_count = 0`
- `decision = PROCEED_TO_TASK_5`
