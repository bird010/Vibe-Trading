# Shadow A：冻结 R40 的前瞻影子验证

- 策略 ID：`ai_rotation_r40_single_name_ceiling`
- 冻结 ceiling：`0.5`
- strategy version：`sv-9525ec8754d100c0`
- R40 source SHA-256：`12ec4e737be6438260b0b0f9c7ce74525d5514c7fdd895cf9e7f65edf61a2655`
- R39 control source SHA-256：`9f9bcc49494adeb5e54f169b85605a078027098b9db3281fa895f18bf1c5d72c`
- Shadow 状态：`STARTED`
- 资格状态：`INSUFFICIENT_FORWARD_EVIDENCE`
- shadow_manifest SHA-256：`3c6bf73e027aa8f34e096ae3078c77cca6e6e3cfb6f96fd64b9549d4d191382b`

## 启动与资格

本次仅完成冻结版本与 Shadow deployment 启动边界。启动不等于资格通过；资格策略固定要求至少 104 周真实前瞻观察、已完成 rebalance cycle、完整证据和人工批准。当前真实 forward 服务、U1 identity 和行情输入不可用，因此资格状态明确为 `INSUFFICIENT_FORWARD_EVIDENCE`，`promotion_allowed=false`，没有声称 qualified。

## 决策与执行边界

本次只完成 `ShadowDecisionService`、`ShadowExecutionService` 和 `ShadowRunScheduler` 的bootstrap binding；没有 signal/行情，因此没有调用 decision seal 或 execution，也没有声称已完成一个 runtime cycle。正式输入到位后，决策与执行将沿用既有的不同幂等边界。本次 runtime binding：`{'decision_service': 'ShadowDecisionService', 'decision_provider': 'StoreScheduledSignalProvider', 'execution_service': 'ShadowExecutionService', 'execution_configured': False, 'scheduler': 'ShadowRunScheduler', 'runtime_binding_status': 'BOOTSTRAP_ONLY', 'cycle_status': 'unavailable_no_real_signal_or_market_data'}`。本次没有可用 signal 或执行行情，故没有伪造 Shadow decision、order、attempt、trade 或收益数据；连续账户的初始状态、ideal/executable NAV 字段和 append-only 空事件文件已写入。独立 `shadow_integrity.json` sidecar 保存 manifest、报告和全部预期 artifact 的 hash，用于复跑时检测完整性。

## 输入与限制

- U1 identity：`missing`
- R39 control identity：`missing`
- 104 周前瞻证据：`unavailable`
- 事件收益、尾部损失、worst week、MDD、换手和执行延迟：`unavailable`

## 最小改动自评

仅新增 Shadow A 启动入口、focused tests 和中文报告；复用既有 FrozenStrategyVersion、ShadowDecisionService、ShadowExecutionService、ShadowRunScheduler 及 shadow artifact contracts。未修改 R39/R40 策略逻辑、公共 execution ledger、平台架构或历史记录，50% ceiling 未调参。
