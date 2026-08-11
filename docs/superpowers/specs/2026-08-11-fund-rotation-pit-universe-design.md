# 基金轮动 PIT Universe 与历史基金主数据设计

**目标：** 确保任意信号日只使用当时已存在、尚未终止、当时属于目标类型且当时可知的基金信息。

## 1. 当前问题

当前基金轮动主路径只从 `dim_fund` 读取 `ts_code`、`name`、`list_date`，静态名称过滤后只判断 `list_date <= signal_date`。它不能证明已清盘基金仍保留、名称和类型是历史值、当前表没有只保留现存基金，也不能证明某条元数据在信号日已经可知。

**通俗解释：** 用今天仍营业的商店名单回看十年前，即使剔除当时尚未开业的商店，也提前知道了哪些商店后来活了下来。

**不修改的后果：** 可能产生幸存者偏差、当前名称污染和产品类型污染，使长期收益、代表 ETF 质量和策略稳定性被系统性高估。

## 2. 数据模型

新增 `dim_fund_pit.lance`，每行表示一个有效时间段：

```text
ts_code
effective_from
effective_to
source_updated_at
list_date
delist_date
fund_status
name
fund_type
asset_class
tracking_index
exchange
```

交易属性可以同表存储或由独立 PIT 规则表提供：

```text
settlement_rule
price_limit_rule
lot_size
tick_size
currency
```

第一阶段不强制补齐管理费、托管费、规模和折溢价；这些属于代表 ETF 质量模型，不应阻塞最小 PIT 资格改造。

## 3. 时间语义

一条记录在 `effective_from <= signal_date < effective_to` 时有效；`effective_to` 为空表示截至快照仍有效。产品资格还必须满足：

```text
list_date <= signal_date
delist_date 为空或 signal_date < delist_date
fund_status 在当日允许进入候选池
```

`source_updated_at` 记录信息何时进入数据系统，防止把后来更正的字段冒充为过去已经可知。

## 4. 三层 Universe

1. **Fund Master Universe：** 产品身份、上市终止、历史状态和历史分类。
2. **Strategy Universe：** 根据 `UniversePolicy` 限定资产类别、基金类型、交易所和产品范围。
3. **Tradable Universe：** 根据当日行情、复权、停牌、成交量和执行规则判断能否交易。

三层必须保存不同排除原因，避免把“不属于策略”“数据缺失”和“当日无法成交”混为一谈。

## 5. 核心接口

```python
class PITFundMaster:
    def instruments_at(
        self,
        signal_date: str,
        snapshot_version: int,
    ) -> tuple[FundInstrumentVersion, ...]:
        ...

class UniverseResolver:
    def resolve(
        self,
        signal_date: str,
        strategy_policy: UniversePolicy,
        causal_view: CausalDataView,
    ) -> UniverseResolution:
        ...
```

`UniverseResolution` 包含 `eligible`、三层 exclusions、source snapshot、coverage metrics 和 quality status。每条排除记录必须有稳定 `reason_code`。

## 6. 数据审计与历史回填

### 6.1 先审计，不预设一定需要全量重建

对现有 `dim_fund.lance` 与历史基金行情代码做逐年差集，回答历史行情出现过多少基金、master 能解释多少、哪些终止基金仍保留、哪些历史代码缺失、名称和类型是否只有当前值、最早可靠覆盖年份是什么。

代码只能证明当前缺少防护，不能单独证明实际数据已经发生严重幸存者偏差，因此必须先输出审计证据。

### 6.2 根据审计结果选择回填路径

- master 已保留终止基金：补充有效期和历史字段。
- master 只保留现存基金：从可靠来源回填历史终止基金。
- 某些年份无法证明完整：标记 `PIT_UNVERIFIED`，不得发布正式策略排名。

## 7. 数据质量指标

每个快照输出：

```text
historical_market_codes
master_codes
missing_master_codes
unexpected_master_codes
listed_count
delisted_count
active_count
eligible_count
missing_delist_date_count
unknown_status_count
unknown_type_count
overlapping_effective_range_count
```

逐年输出 `historical_traded_fund_count / master_reconstructed_count / coverage_ratio`。首版采用保守门禁：存在历史行情、但 master 完全无法解释的代码时，快照为 `PIT_INVALID`。明确分类为非目标 ETF 后可以排除，不能直接忽略。

## 8. 失败与降级

- 有效期重叠、同日匹配多条记录、`list_date > delist_date`：快照失败。
- 状态或类型未知：默认不进入投资池并记录原因。
- `delist_date` 缺失：不能自动等同仍存续，标记待核验。
- 覆盖门禁未通过：允许探索回测，但状态必须为 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`。
- 查询读取 `signal_date` 后生效的数据：运行 INVALID。

## 9. 兼容迁移

迁移期并行生成 `legacy_universe_result / pit_universe_result / universe_difference`。差异报告说明新增和移除 ETF、首个净值差异日期、受影响交易和收益。禁止静默切换；验收后主路径只允许 PIT Resolver。

## 10. 测试

- 上市前不可入选，退市或清盘后不可入选，退市前可按当时状态入选。
- 历史名称和类型只在生效日后变化。
- 有效期重叠或同日多版本必须失败。
- 固定快照后，新增数据不能改变运行结果。
- 历史行情有代码而 master 缺失时门禁失败。
- legacy 与 PIT 差异报告可重复生成。
- 所有排除项拥有稳定原因码。

## 11. 验收

- 目标回测年份全部生成逐年覆盖报告。
- 每个历史代码都能解释为合格、明确排除或数据缺陷。
- 同一快照重复查询产生完全相同的 Universe。
- 任一 `signal_date` 不读取之后才生效或可获得的信息。
- 审计通过后研究状态才能升级为 `DATA_VERIFIED`。
