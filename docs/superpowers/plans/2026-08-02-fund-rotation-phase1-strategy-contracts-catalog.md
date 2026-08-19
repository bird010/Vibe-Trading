# Phase 1：完整策略契约与 Catalog 实施计划

> **执行要求：** 使用 `executing-plans` 技能按任务顺序实施；本阶段只建立内部契约，不切换公开创建回测接口。

**目标：** 让完整基金轮动策略成为唯一插件单位，并能通过显式 Catalog 发现、校验配置、解析数据需求和生成可重放源码快照。

**架构：** 使用 Protocol/ABC 表达策略与 session 行为，使用 Pydantic 表达每个策略自己的配置；Catalog 由启动时显式白名单构建，不动态扫描或导入任意目录。

**技术栈：** Python、Pydantic、pytest、importlib.resources。

---

## Task 1：定义公共契约和值对象

**文件：**

- 新建：`agent/backtest/fund_rotation/contracts.py`
- 新建：`agent/tests/fund_rotation/test_strategy_contracts.py`

**接口：**

```python
class DecisionKind(str, Enum):
    SET_TARGETS = "SET_TARGETS"
    HOLD_TARGETS = "HOLD_TARGETS"
    INVALID = "INVALID"

class FundRotationStrategy(Protocol):
    descriptor: FundRotationStrategyDescriptor
    config_model: type[BaseModel]
    def resolve_requirements(self, config: BaseModel) -> StrategyDataRequirements: ...
    def create_session(
        self,
        initialization: StrategyInitializationContext,
        config: BaseModel,
    ) -> "FundRotationStrategySession": ...

class FundRotationStrategySession(Protocol):
    def scheduled_dates(
        self,
        calendar: tuple[str, ...],
        simulation_start_date: str,
        evaluation_end_date: str,
    ) -> tuple[str, ...]: ...
    def evaluate(self, context: StrategyDecisionContext) -> TargetWeightDecision: ...
    def finalize(self) -> StrategyDiagnostics: ...
```

**步骤：**

1. 为配置拒绝额外字段、权重和为 1、非法状态组合、首个 HOLD 语义写失败测试。
2. 定义不可变 `FundRotationStrategyDescriptor`、`StrategyDataRequirements`、`StrategyDecisionContext`、`TargetWeightDecision`、`StrategyDiagnostics` 和 artifact role 值对象；`StrategyDecisionContext` 必须提供 `signal_date`、受控 `CausalDataView` 和只读 `previous_target_weights`，不得提供实际成交、现金或残余订单。
3. 明确 `TargetWeightDecision` 必含 `decision_id/signal_date/action/target_weights/cash_weight/reason_code/quality_status/diagnostics`；`INVALID` 必须包含稳定错误码和可读原因，`HOLD_TARGETS` 不携带新权重。
4. 实现 Runner 契约校验：权重有限、非负、总和正确、代码属于当日合格池、`decision_id` 在子运行内唯一、相同语义输出不依赖输入顺序；违约使用 `STRATEGY_CONTRACT_VIOLATION` 终止子运行。
5. 实现协议和校验，不引入聚类字段；测试纯数据预热阶段不调用 `evaluate()`，满足预热后任何 `INVALID`（包括评价开始日前）均终止。
6. 运行目标测试及全套基金轮动测试。

**提交：** `feat(fund-rotation): define complete strategy contracts`

## Task 2：建立基准策略包和 Pydantic 配置

**文件：**

- 新建：`agent/backtest/fund_rotation/strategies/__init__.py`
- 新建：`agent/backtest/fund_rotation/strategies/correlation_all_members/__init__.py`
- 新建：`agent/backtest/fund_rotation/strategies/correlation_all_members/config.py`
- 新建：`agent/backtest/fund_rotation/strategies/correlation_all_members/strategy.py`
- 新建：`agent/tests/fund_rotation/test_correlation_all_members_strategy.py`

**步骤：**

1. 把当前 `FundRotationConfig` 字段逐项写成 Pydantic 配置契约测试，包括默认值、范围、旧 JSON 映射和禁止未知字段。
2. 实现 `CorrelationAllMembersConfig`，属性名先与现有调用方一致，避免同阶段大范围重写。
3. 实现作为迁移 baseline 的 `CorrelationAllMembersStrategy`/session 外壳；信号生成暂时可委托现有纯函数，执行仍不切换。
4. 断言 JSON Schema 含标题、类型、默认值、上下限和中文说明。
5. 对旧 dataclass 做单向转换适配，禁止新代码反向依赖旧类型。

**提交：** `feat(fund-rotation): add baseline strategy package and schema`

## Task 3：实现显式白名单 Catalog

**文件：**

- 新建：`agent/backtest/fund_rotation/catalog.py`
- 新建：`agent/tests/fund_rotation/test_strategy_catalog.py`

**接口：**

```python
class FundRotationStrategyCatalog:
    def list(self) -> tuple[StrategyCatalogEntry, ...]: ...
    def require(self, strategy_id: str) -> RegisteredFundRotationStrategy: ...
    def resolve(
        self,
        strategy_id: str,
        raw_params: Mapping[str, JsonValue],
    ) -> ResolvedStrategyBinding: ...
```

**步骤：**

1. 写未知 ID、重复 ID、空版本、接口版本不兼容、非法 schema、实现快照损坏和顺序不稳定测试。
2. 定义 `RegisteredFundRotationStrategy`、可持久化 `ResolvedFundRotationStrategySpec` 和同时绑定二者的 `ResolvedStrategyBinding`；内存 factory 不得进入 JSON、请求或哈希。
3. 以显式构造器参数/常量注册 `CorrelationAllMembersStrategy`；不得使用目录扫描或任意字符串 import。服务启动时立即固定所有注册策略的实现快照，运行期间不得重新读取策略源码磁盘。
4. 实现完整 `resolve()`：检查 ID → Pydantic 校验并拒绝未知字段 → 补齐默认值 → 跨字段校验 → 规范化 JSON → schema/config 哈希 → 解析并校验需求及哈希 → 绑定启动时实现快照 → 返回 `ResolvedStrategyBinding`。测试省略默认值和显式默认值产生相同哈希。
5. Catalog 输出按 `strategy_id` 稳定排序，返回不可变值。
6. 统一错误码：`FUND_ROTATION_STRATEGY_NOT_FOUND`、`FUND_ROTATION_CONFIG_INVALID`、`FUND_ROTATION_INTERFACE_INCOMPATIBLE`、`FUND_ROTATION_DUPLICATE_STRATEGY_ID`、`FUND_ROTATION_STRATEGY_SNAPSHOT_INVALID`；未知策略、配置和接口版本错误必须在后台任务创建前结构化返回。
7. 测试启动时即发现重复和损坏插件，而不是运行到一半才失败。

**提交：** `feat(fund-rotation): add explicit strategy catalog`

## Task 4：配置相关的数据需求解析

**文件：**

- 修改：`agent/backtest/fund_rotation/contracts.py`
- 修改：`agent/backtest/fund_rotation/strategies/correlation_all_members/strategy.py`
- 新建：`agent/tests/fund_rotation/test_data_requirements.py`

**步骤：**

1. 写测试证明同一策略不同配置可产生不同历史长度/字段需求。
2. `StrategyDataRequirements` 至少声明 `required_datasets`、`required_fields`、`warmup_trade_days`、频率和是否需要基准；解析必须是只依赖已验证配置的纯函数。
3. correlation-all-members baseline 根据 `correlation_lookback_weeks`、ADV20、基准配置解析需求，不读取全表后再裁剪。
4. 合并多个需求时采用字段并集、历史长度最大值；冲突频率必须显式失败。
5. 不在公共需求对象中加入聚类门禁参数。

**提交：** `feat(fund-rotation): resolve config-dependent data requirements`

## Task 5：实现源码和配置快照哈希

**文件：**

- 新建：`agent/src/stockpred/fund_rotation/strategy_snapshot.py`
- 新建：`agent/tests/fund_rotation/test_strategy_snapshot.py`

**步骤：**

1. 写稳定性测试：文件枚举顺序、路径分隔符和 JSON key 顺序不同不改变哈希；服务启动后即使磁盘策略文件改变，本进程内绑定的实现快照和哈希也不得改变。
2. 写敏感性测试：策略源码、配置值、框架源码或数据快照任一变化，对应哈希必须变化。
3. 只快照显式声明的策略包文件和公共框架文件；排除 `__pycache__`、测试缓存和运行输出。
4. manifest 分开记录设计 §19 的正式字段 `strategy_implementation_hash`、`framework_implementation_hash`、`resolved_config_hash`、`data_snapshot_fingerprint`，并结合研究契约和执行契约生成 `run_identity_hash`；后续批次比较直接复用这些字段，不另设第二套源码哈希别名。
5. 记录 Python、pandas、Lance 和应用版本用于跨运行解释，但不把机器绝对路径纳入哈希。

**提交：** `feat(fund-rotation): snapshot strategy framework and config`

## Task 6：阶段整体验收

**文件：**

- 修改：`agent/tests/fund_rotation/test_review_contracts.py`

**步骤：**

1. 增加架构测试：公共 contracts/catalog 不得 import `clustering.py`、`correlation.py` 或具体策略内部模块。
2. 增加契约测试：策略拿不到原始 Lance 路径和可变 pipeline config。
3. 运行 mypy/项目既有静态检查（若仓库已有命令）以及基金轮动全套测试。
4. 确认现有公开 POST 行为未切换。

**验证：**

```powershell
E:\anaconda3\envs\VibeTrading\python.exe -m pytest agent\tests\fund_rotation -q
rg -n "clustering|correlation" agent\backtest\fund_rotation\contracts.py agent\backtest\fund_rotation\catalog.py
git diff --check
```

**提交：** `test(fund-rotation): verify strategy catalog foundation`

## Phase 1 出口门禁

- Catalog、配置 schema、数据需求和快照测试全部通过。
- baseline 是完整策略对象，但尚未改变生产 API 路径。
- 公共契约中没有任何聚类专用概念。
