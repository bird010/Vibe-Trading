# R39 逐步融入 R57 改动与调参实验设计

## 目标

以当前研究 Champion `ai_rotation_r39_incumbent_carry` 为基线，将 R57 的变化拆成三个阶段逐步融入，并在每个阶段的基础版上进行受控调参。每个版本都是独立的新策略和独立回测证据，不修改 R39 或已经存在的策略实现。

## 既定基线与边界

- Champion：`ai_rotation_r39_incumbent_carry`。
- R57 参考实现：`ai_rotation_r57_three_factor_representative`。
- 既有 R57 全量回测仅作为探索证据，不作为 OOS；其结果显示需要拆分归因。
- 研究区间、PIT 数据、公共 Runner、执行合同、评价政策和 Champion gates 由 `optimizing-fund-rotation` 实验契约冻结。
- 每个 paired batch 恰好包含当前 Champion 与一个 Challenger。
- 不修改 R39 默认值、公共 Runner、数据契约、执行语义或评价政策。

## 逐阶段主变化

### Round 01：只替换信号

在 R39 的代表生命周期和组合/承接规则上，只引入 R57 的日频三因子信号。基础版使用 R57 预注册公式、窗口、权重和阈值默认值；R39 的 Top-3、半仓试探和 incumbent carry 保持不变。

### Round 02：只替换代表生命周期

以 Round 01 的晋级 Champion 为基础，只引入 R57 的代表锁定、26 周重聚类和硬失效替换规则；保持上一阶段的信号与 R39 组合规则不变。

### Round 03：只替换组合规则

以 Round 02 的晋级 Champion 为基础，只引入 R57 的 Top-1、满仓和阈值切换组合规则；保持前两阶段已晋级的信号与代表生命周期不变。

## 调参规则

每个 Round 先实现一个基础版，再视代码与回测证据决定是否产生调参版，最多默认 3 个：

- `round01_baseline`、`round01_tune1`、`round01_tune2`、`round01_tune3`
- `round02_baseline`、`round02_tune1`、`round02_tune2`、`round02_tune3`
- `round03_baseline`、`round03_tune1`、`round03_tune2`、`round03_tune3`

调参版本必须满足：

1. 每个版本新增唯一策略 ID，不能覆盖或修改既有策略。
2. 每个版本只改变一个预注册参数或一个明确的单变量机制；禁止宽范围参数搜索。
3. 调参候选必须由上一版本的 fold、持仓、现金、换手、成本和执行证据提出，并在 selection interval 内预先记录。
4. 不得使用 consumed confirmation interval 选择参数，不得事后按全区间最高分挑选。
5. 每个版本单独完成行为测试、实现报告、独立审查和 paired backtest；失败、无改进或不可比均消耗一个授权版本预算并写入 ledger。
6. 如果没有新的可证伪假设或参数证据，不强行生成 tune2/tune3。

候选优先顺序为：先修复明确的执行/风险问题，再调整单一信号或组合参数；不得同时改变窗口、权重、阈值、候选数量和权重模式。

## 选择与晋级

每个版本相对当时 Champion 应用相同 Champion gates：Validation Sharpe 更高、年化收益不低、最大回撤恶化不超过 1 个百分点、有效 fold 中 Sharpe 多数获胜，且无 PIT、执行质量、完整性、可比性或实现门禁失败。

同一 Round 的基础版和调参版按注册顺序逐个验证；只有通过 gates 的版本才成为下一版本的 Champion 候选。若所有版本均失败，保留当前 Champion，并进入下一主变化阶段时以当前 Champion 为基础。

## 产物

实验根目录至少保存：`experiment_spec.json`、`fold_manifest.json`、append-only `experiment_ledger.jsonl`、`champion.json`，以及每个版本的 `analysis.md`、`design.md`、`implementation_report.md`、`review.json`、`test_results.md`、`backtest_request.json`、`backtest_result.json` 和 `decision.json`。最终保留 `final_candidate.json`、确认报告和不少于 104 周的 forward-shadow 预注册。

## 非目标

- 不直接把 R57 全量实现复制到 R39。
- 不修改 R39、R57 原实现或公共执行层。
- 不因调参版本增加而扩大到未授权的 R04 或其他策略方向。
- 不把研究 Champion 晋级等同于部署资格。
