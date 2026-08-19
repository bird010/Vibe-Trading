# 基金轮动 PIT Universe 与历史基金主数据设计

**目标：** 确保任意信号日只使用当时已存在、尚未终止、当时属于目标类型且当时可知的基金信息。

## 1. 当前问题

当前基金轮动主路径只从 `dim_fund` 读取 `ts_code`、`name`、`list_date`，静态名称过滤后只判断 `list_date <= signal_date`。它不能证明已清盘基金仍保留、名称和类型是历史值、当前表没有只保留现存基金，也不能证明某条元数据在信号日已经可知。

**通俗解释：** 用今天仍营业的商店名单回看十年前，即使剔除当时尚未开业的商店，也提前知道了哪些商店后来活了下来。

**不修改的后果：** 可能产生幸存者偏差、当前名称污染和产品类型污染，使长期收益、代表 ETF 质量和策略稳定性被系统性高估。

## 2. 数据模型

新增追加式修订表 `dim_fund_pit.lance`。每行表示“某项事实在现实中的有效区间”以及“系统从何时开始知道该版本”：

```text
ts_code
valid_from
valid_to
known_from
revision_id
source_id
source_record_id
source_published_at（来源确实提供时才记录）
ingested_at
list_date
delist_date
fund_status
name
fund_type
asset_class
tracking_index
exchange
```

`known_to` 可由同一业务记录下一版本的 `known_from` 推导，首版不强制物理存储。`source_record_id / source_published_at` 无法从来源可靠获得时必须留空并降级，禁止用抓取时间伪造发布时间。现有 Lance version 或 snapshot fingerprint 继续负责固定整个数据集合，不在每行重复存储 `snapshot_id`。

交易属性可以同表存储或由独立 PIT 规则表提供：

```text
settlement_rule
price_limit_rule
lot_size
tick_size
currency
```

第一阶段不强制补齐管理费、托管费、规模和折溢价；这些属于代表 ETF 质量模型，不应阻塞最小 PIT 资格改造。

## 3. 双时间语义

一条记录必须同时满足：

```text
valid_from <= signal_date < valid_to
known_from <= knowledge_cutoff
```

`valid_to` 为空表示事实截至快照仍有效。若同一业务记录在 `knowledge_cutoff` 前存在多个修订版本，只选择当时最后已知版本。产品资格还必须满足：

```text
list_date <= signal_date
delist_date 为空或 signal_date < delist_date
fund_status 在当日允许进入候选池
```

明确支持两种查询模式：

```text
AS_WAS_KNOWN：按 knowledge_cutoff 重现当时系统可知版本，可用于因果回测
LATEST_RESTATED：使用快照中后来修订的历史真值，只能用于研究、对账或审计
```

如果历史上没有保存来源修订链，就不能仅靠补字段恢复“当时可知”。对应区间标记 `KNOWLEDGE_TIME_UNVERIFIED`，不得升级为正式 PIT 资格。

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
        knowledge_cutoff: str,
        snapshot_version: int,
        mode: PITQueryMode = PITQueryMode.AS_WAS_KNOWN,
    ) -> tuple[FundInstrumentVersion, ...]:
        ...

class UniverseResolver:
    def resolve(
        self,
        signal_date: str,
        knowledge_cutoff: str,
        strategy_policy: UniversePolicy,
        causal_view: CausalDataView,
    ) -> UniverseResolution:
        ...
```

`UniverseResolution` 包含 `eligible`、三层 exclusions、source snapshot、`knowledge_cutoff`、query mode、coverage metrics 和 quality status。每条排除记录必须有稳定 `reason_code`。

## 6. 数据审计与历史回填

### 6.1 先审计，不预设一定需要全量重建

对现有 `dim_fund.lance` 与历史基金行情代码做逐年差集，回答历史行情出现过多少基金、master 能解释多少、哪些终止基金仍保留、哪些历史代码缺失、名称和类型是否只有当前值、最早可靠覆盖年份是什么。

代码只能证明当前缺少防护，不能单独证明实际数据已经发生严重幸存者偏差，因此必须先输出审计证据。

### 6.2 根据审计结果选择回填路径

- master 已保留终止基金：补充有效期和历史字段。
- master 只保留现存基金：从可靠来源回填历史终止基金。
- 某些年份无法证明完整：标记 `PIT_UNVERIFIED`，不得发布正式策略排名。

### 6.3 独立来源交叉审计

内部历史行情代码与 Fund Master 的差集只能发现两者不一致，不能发现两者共同遗漏。因此正式 PIT 资格必须至少引入一种具有独立来源谱系的历史名册，例如交易所上市/终止名单、监管登记信息或基金清盘公告。

交叉审计输出：

```text
source_a_codes / source_b_codes
only_in_a / only_in_b
status_conflicts / date_conflicts / classification_conflicts
reconciliation_status / resolution_reason
```

两个供应商如果共享同一上游，不算独立来源。首版无需长期维护两套完整商业主数据，但必须有覆盖目标市场的权威历史名册，并对冲突留下可追溯解释。

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
overlapping_valid_range_count
overlapping_knowledge_range_count
knowledge_time_unverified_count
cross_source_missing_count
status_conflict_count
date_conflict_count
```

逐年输出 `historical_traded_fund_count / master_reconstructed_count / coverage_ratio`。首版采用保守门禁：存在历史行情、但 master 完全无法解释的代码时，快照为 `PIT_INVALID`。明确分类为非目标 ETF 后可以排除，不能直接忽略。

## 8. 失败与降级

- 有效期重叠、同一 `knowledge_cutoff` 匹配多条无确定修订顺序的记录、`list_date > delist_date`：快照失败。
- 状态或类型未知：默认不进入投资池并记录原因。
- `delist_date` 缺失：不能自动等同仍存续，标记待核验。
- 覆盖门禁未通过：允许探索回测，但状态必须为 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`。
- `AS_WAS_KNOWN` 查询读取 `signal_date` 后生效或 `knowledge_cutoff` 后才知的数据：运行 INVALID。
- 缺少可验证修订历史或独立来源审计：允许探索运行，但资格不得高于 `PIT_UNVERIFIED`。

## 9. 兼容迁移

迁移期并行生成 `legacy_universe_result / pit_universe_result / universe_difference`。差异报告说明新增和移除 ETF、首个净值差异日期、受影响交易和收益。禁止静默切换；验收后主路径只允许 PIT Resolver。

## 10. 测试

- 上市前不可入选，退市或清盘后不可入选，退市前可按当时状态入选。
- 历史名称和类型只在生效日后变化。
- 后来修订的退市日期在修订被知晓前不可见；`LATEST_RESTATED` 与 `AS_WAS_KNOWN` 的差异可复算。
- 有效期重叠或同一知识时点存在无法排序的多版本必须失败。
- 固定快照后，新增数据不能改变运行结果。
- 历史行情有代码而 master 缺失时门禁失败。
- 内部行情与 master 同时遗漏、但独立历史名册存在的代码会被交叉审计发现。
- legacy 与 PIT 差异报告可重复生成。
- 所有排除项拥有稳定原因码。

## 11. 验收

- 目标回测年份全部生成逐年覆盖报告。
- 每个历史代码都能解释为合格、明确排除或数据缺陷。
- 同一快照重复查询产生完全相同的 Universe。
- 任一正式查询均显式传入 `signal_date / knowledge_cutoff / snapshot_version / mode`，不读取之后才生效或才可获得的信息。
- 有效时间、知识时间和独立来源审计通过后，`DataSnapshotQualification` 才能升级为 `VERIFIED`。
