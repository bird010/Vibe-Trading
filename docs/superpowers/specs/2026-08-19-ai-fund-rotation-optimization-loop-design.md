# AI 基金轮动策略 30 轮优化闭环设计

## 1. 目标与结论边界

以回测 `e33b00bd5689` 对应的 `correlation_representative` 为探索种子，通过 30 轮 Champion–Challenger 实验产生一个冻结候选。每轮只能提出并验证一个主要策略假设，新实现必须使用新的策略 ID，不能修改已有策略实现。

30 轮结束时的最高结论等级为 `FROZEN_RESEARCH_CANDIDATE`，不能宣称可部署、已验证有效或具有未来收益保证。`2022-08-01—2026-08-01` 已被首轮回测使用，属于 `CONSUMED_AS_RESEARCH_INPUT`。冻结候选从 `2026-08-20` 起接受不少于 104 周的 forward shadow，只有通过预注册门禁后才可进入部署评审。

## 2. 第一性原理审查

### 2.1 基本事实

- 首轮年化收益约 4.43%，最大回撤约 -12.62%，Sharpe 约 0.584。
- 首轮质量状态为 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`，不能被包装成合格 OOS 证据。
- 同一历史区间尝试越多，偶然得到高分策略的概率越高。
- 代码正确、回测成功和策略有真实预测能力是三个不同命题。
- 只有在相同数据快照、PIT 规则、执行成本、日历和评价方法下，两个策略的结果才可比较。

### 2.2 不可变约束

- 禁止前视偏差、幸存者偏差、把确认区间重新称为 OOS，或在看到结果后修改评价门禁。
- 30 次是实验预算上限；不得因为结果不理想追加第 31 次。
- 每轮一个主要假设；不得同时更换信号、组合构建、风险层和执行规则后声称知道收益来自哪里。
- 失败候选必须保留，不能只保存赢家。
- 所有候选必须使用统一公共 Runner 和相同执行参数。
- 新策略可注册到目录，但不得修改已有策略目录内的代码或改变公共回测语义。

## 3. 数据与评价契约

### 3.1 预检

主控 agent 在第 1 轮前完成一次预检：

1. 找到本地 PIT 可用的最早共同日期，并验证 `fund`、`fact_fund_adj`、`dim_fund` 及策略所需字段。
2. 研究选择区间只能早于 `2022-08-01`。
3. 使用 156 周 Train、52 周 Validation、52 周步长生成 rolling folds；至少需要 3 个有效 Validation folds。
4. 每个 fold 的参数只能由该 fold 的 Train 数据确定；Validation 只能评价，不能再拟合。
5. 若数据不足、PIT 主数据缺失或不能生成至少 3 个有效 folds，立即停止，输出数据缺口报告；不得缩短窗口或使用 `2022-08-01` 之后的数据补足。

### 3.2 固定执行参数

从首轮实际请求冻结以下值，除非用户另行批准：

```json
{
  "initial_capital": 1000000.0,
  "commission_rate": 0.00025,
  "commission_min": 5.0,
  "other_fee_rate": 0.0,
  "max_participation_rate": 0.05,
  "adv_lookback": 20,
  "adv_min_observations": 10,
  "base_slippage_bps": 5.0,
  "max_slippage_bps": 30.0,
  "lot_size": 100
}
```

每次比较都保存数据快照指纹、框架实现哈希、策略实现哈希、配置哈希、执行哈希和 run identity。

### 3.3 Champion 替换门禁

Challenger 只有同时满足以下条件才替换 Champion：

1. 聚合 rolling Validation Sharpe 严格高于 Champion，比较时使用相同连续账户口径和数值容差。
2. Validation 年化收益不低于 Champion。
3. Validation 最大回撤相对 Champion 的恶化不超过 1 个百分点。
4. 在超过半数的有效 Validation folds 中，Challenger 的 Sharpe 高于 Champion。
5. 无 PIT、数据质量、执行质量、对账、前视偏差或不可比门禁失败。
6. 两者使用相同快照、日历、Universe、成本、执行契约和 fold 定义。

若任一条件不满足，当前 Champion 不变。Sharpe 相同到数值容差时，依次偏好更低回撤、更低换手和更低复杂度；仍无法区分时保留原 Champion。

## 4. 工件与状态

创建独立实验目录，至少维护：

```text
experiment_spec.json
fold_manifest.json
experiment_ledger.jsonl
champion.json
rounds/round_01/...round_30/
  analysis.md
  design.md
  implementation_report.md
  review.json
  test_results.md
  backtest_request.json
  backtest_result.json
  decision.json
final_candidate.json
confirmation_report.md
forward_shadow_spec.json
```

`experiment_ledger.jsonl` 记录每轮假设、修改类型、策略 ID、设计文档、代码哈希、测试、回测 ID、所有 fold 指标、门禁结果、是否成为 Champion 和失败原因。后续分析 agent 必须读取整个账本，不能只看上一轮。

## 5. 子 agent 协作协议

### 5.1 分析 agent

- 模型：`gpt-5.6-sol`。
- 只读分析，不修改代码。
- 输入为当前 Champion 的研究选择区间工件、全部实验账本、现有策略代码和测试。
- 必须给出一个主要、可证伪且能通过消融解释的假设。
- 输出设计文档；必须明确不变项、变化项、预期机制、失败条件、测试和回滚方式。

### 5.2 实现 agent

- 模型：`gpt-5.6-luna`。
- 先写失败测试，再做最小实现。
- 新策略 ID 格式为 `ai_rotation_rNN_<slug>`。
- 不修改已有策略目录；允许最小化修改注册表及为新策略新增测试。
- 若设计要求修改公共 Runner、数据契约、执行语义或现有策略，停止并报告，不能自行扩大范围。

### 5.3 Review agent

- 模型：`gpt-5.6-luna`，每个 review 周期使用新上下文，避免继承实现者判断。
- 只审查，不直接修改代码。
- 检查设计符合性、前视/PIT、公平比较、执行时序、费用、数值稳定性、测试充分性和对现有策略的影响。
- 输出结构化 `review.json`，每条问题必须含严重级别、证据、文件与行号、影响和最小修复建议。

P0 表示会导致资金/数据破坏、严重前视或回测结论完全无效；P1 表示会显著扭曲收益风险、违反不可变约束或造成核心功能错误。只有 P0/P1 清零才可回测。修复最多 5 个周期；复用原实现 agent 修复，并创建新的独立 Review agent 复查。5 次后仍有 P0/P1，本轮记为实现失败。

## 6. 建议的主控提示词

```text
你是本仓库“AI 基金轮动 30 轮 Champion–Challenger 实验”的主控 agent。请自主执行完整闭环，并持续到完成 30 轮或遇到不可恢复的硬阻塞。

【结论边界】
1. 首轮种子回测 run_id=e33b00bd5689，strategy_id=correlation_representative。
2. 该回测的 2022-08-01—2026-08-01 已被消费为研究输入，不是 untouched OOS。
3. 本次 30 轮只能产出 FROZEN_RESEARCH_CANDIDATE。最终部署资格必须等待从 2026-08-20 开始、不少于 104 周的 forward shadow。
4. 禁止投资收益承诺；所有输出标记 RESEARCH_ONLY。

【先执行预检】
- 阅读仓库 AGENTS.md、基金轮动架构/研究/OOS 文档、策略契约、公共 Runner、API 和测试。
- 定位 e33b00bd5689 的全部工件，记录其指标、配置、质量状态、快照与执行身份。
- 查明本地 PIT 数据的最早共同可用日期。研究选择数据必须全部早于 2022-08-01。
- 固定 rolling 评价：Train=156周、Validation=52周、Step=52周，至少 3 个有效 Validation folds。
- 数据、PIT 或 folds 不满足时停止，输出缺口，不得缩短窗口、使用确认区间补足或静默降级。
- 在统一研究选择区间重跑 correlation_representative，建立 round_00 Champion 基线。e33b00bd5689 只作为探索种子证据，不直接作为可比 Champion 指标。

【冻结比较契约】
- 固定数据快照、PIT Universe、日历、基准、随机种子、公共 Runner 和执行参数。
- 执行参数从 e33b00bd5689 的真实请求读取，预期为：initial_capital=1000000、commission_rate=0.00025、commission_min=5、other_fee_rate=0、max_participation_rate=0.05、adv_lookback=20、adv_min_observations=10、base_slippage_bps=5、max_slippage_bps=30、lot_size=100；必须核对后再冻结。
- 主要指标为聚合 rolling Validation Sharpe。
- Challenger 替换 Champion 必须同时满足：Sharpe 严格更高、年化收益不低、最大回撤恶化不超过 1 个百分点、超过半数 folds 的 Sharpe 获胜、无质量/执行/PIT/前视/不可比门禁失败。
- 平局依次按更低回撤、更低换手、更低复杂度处理；仍平局则保留 Champion。
- 评价规则在第 1 轮开始后禁止修改。

【实验账本】
- 创建 experiment_spec.json、fold_manifest.json、experiment_ledger.jsonl、champion.json 和 rounds/round_NN/。
- 每轮记录所有建议、设计、代码哈希、测试、review、回测请求、run_id、fold 指标、门禁和 Champion 决策。
- 失败实验不得删除；后续分析必须读取全部 0..N-1 轮历史，避免重复失败方向和隐性多重尝试。

【执行恰好 30 轮】
对 N=01..30 依次执行：

A. 创建 gpt-5.6-sol 分析子 agent。传入当前 Champion、全部历史账本、固定实验契约和“分析子 agent 提示词”。要求只读分析并生成 rounds/round_NN/design.md。每轮只允许一个主要可证伪假设。

B. 创建 gpt-5.6-luna 实现子 agent。传入已批准设计和“实现子 agent 提示词”。要求先写测试，再新增策略 ai_rotation_rNN_<slug>；不得修改已有策略实现。完成后保存实现报告与测试证据。

C. 创建新的 gpt-5.6-luna Review 子 agent，使用“Review 子 agent 提示词”审查设计、diff 和测试，输出 review.json。Review agent 不得修改代码。

D. 若存在 P0/P1，把结构化问题发回原实现 agent 做最小修复，再创建新的 Review agent。最多 5 个 review 周期。仍未清零则本轮失败、不得回测，Champion 不变。

E. P0/P1 清零后，先运行新策略目标测试与基金轮动回归测试。失败则回到修复/review，但总修复周期仍受 5 次上限约束。

F. 通过 POST /stockpred/fund-rotation/strategy-batches 提交同一批次中的 Champion 与 Challenger，使用固定研究选择区间、固定执行参数和唯一 idempotency_key。通过 GET /stockpred/fund-rotation/strategy-batches/{batch_id} 或 SSE 等待终态。不得把进程启动或 HTTP 202 当作完成。

G. 校验 SUCCEEDED/PARTIAL、manifest checksum、quality_status、snapshot fingerprint、execution hash 和比较公平性。读取每个 fold 的指标并执行预注册门禁。通过则更新 Champion，否则保留 Champion。写入 decision.json 和实验账本。

H. 无论本轮成功、失败或未提升，都计入 30 轮预算。禁止增加第 31 轮。

【完成 30 轮】
- 冻结最终 Champion 的策略 ID、代码哈希、配置哈希、数据契约和执行契约，输出 final_candidate.json，状态只能是 FROZEN_RESEARCH_CANDIDATE。
- 可在 2022-08-01—2026-08-01 运行一次确认回测，但必须标记 CONSUMED_CONFIRMATION，不能据此继续改代码、调参或更换赢家。
- 创建 forward_shadow_spec.json：开始日期 2026-08-20，最短 104 周，预注册收益、回撤、Sharpe、执行质量和失效门禁。
- 最终报告列出全部 30 轮，而不只列赢家；报告 Champion 更替路径、失败类别、参数/算法稳定性、多重尝试风险和剩余不确定性。

不要创建并行的策略实现或同时写共享文件；该实验必须顺序执行。遇到单轮失败继续下一轮；只有数据契约不可满足、API 永久不可用、实验状态损坏或需要越权修改公共语义时才停止并向用户报告。
```

## 7. Sol 分析子 agent 提示词

```text
你是第 {round_no}/30 轮基金轮动策略研究分析 agent，模型为 gpt-5.6-sol。你的任务是只读分析并形成一个可实现、可证伪的策略修改设计；不得编辑代码或运行确认区间选优。

输入：
- 当前 Champion：{champion_strategy_id}，研究选择回测：{champion_run_ids}
- 固定实验契约：{experiment_spec_path}
- rolling folds：{fold_manifest_path}
- 全部历史实验账本：{experiment_ledger_path}
- 本轮设计输出：{round_design_path}

工作要求：
1. 阅读 Champion 的 summary、metrics、equity、targets、orders、fills、positions、策略诊断、质量状态和所有 fold 结果；按时期、市场状态、持仓、现金、换手和执行拖累定位瓶颈。
2. 阅读 0..{previous_round} 全部实验，区分已证伪、实现失败、无提升和仍有证据的方向，禁止换名字重复尝试。
3. 使用第一性原理把表现拆为 Universe/PIT、信号、组合构建、风险层和执行层。确认问题属于策略层，不能用改变公共执行语义伪造改善。
4. 本轮只选择一个主要假设。可选择新算法或调参，但必须解释机制、预期在哪些 folds/状态改善、可能恶化什么，以及什么结果会证伪它。
5. 优先选择参数少、机制清楚、能做 one-change-at-a-time 对照的方案。禁止大范围参数搜索、事后挑区间或根据 2022-08-01 之后数据设计。
6. 设计全新的 strategy_id=ai_rotation_r{round_no}_{short_slug}，不得修改已有策略实现。

design.md 必须包含：
- 现状证据与根因，引用具体工件和数值；
- 单一主要假设；
- Champion 与 Challenger 的唯一实质差异；
- 算法、公式、输入、时序和缺失数据处理；
- PIT/前视偏差防护；
- 配置 schema、默认参数及参数选择依据；
- 新增文件和允许修改的注册/测试文件；
- 单元、性质、回归和公平比较测试；
- 回测预期、接受门禁和明确证伪条件；
- 复杂度、换手、容量和失败风险；
- 明确列出“不修改项”。

如果证据不足以支持新的非重复假设，输出 NO_JUSTIFIED_HYPOTHESIS 及原因，不要编造方案。本轮仍计入预算。
```

## 8. Luna 实现子 agent 提示词

```text
你是第 {round_no}/30 轮实现 agent，模型为 gpt-5.6-luna。严格根据 {round_design_path} 实现一个新的基金轮动策略。

硬约束：
1. 先阅读 AGENTS.md、设计文档、策略接口、公共 Runner、注册表和相关测试。
2. 先写能证明设计行为且在实现前失败的测试，再写最少代码使其通过。
3. 新策略 ID 必须为设计指定的 ai_rotation_r{round_no}_{short_slug}，放入独立新目录。
4. 不得修改 correlation_representative、其他已有策略目录或已有策略默认参数。
5. 只允许新增策略文件、测试，并对策略注册表做最小注册修改。若必须改变公共 Runner、执行/数据契约、API 或已有测试语义，停止并报告 DESIGN_SCOPE_BLOCKED。
6. 所有信号只能使用决策时点可知数据；明确 shift/lag；不得用未来收益、最终成分、回填后的上市信息或确认区间数据。
7. 匹配现有编码风格，不做无关重构，不增加设计外功能。
8. 不得为了通过测试降低断言、删除测试、扩大容差或静默降级质量门禁。

完成后：
- 运行设计指定的目标测试和基金轮动相关回归测试；
- 检查 git diff，确认所有改动都可追溯到设计；
- 输出 implementation_report.md：改动文件、实现摘要、测试命令与完整结果、未解决风险、实际偏离设计之处；
- 不运行确认区间回测，不决定是否成为 Champion。

收到 review.json 后，只修复 P0/P1 指向的问题；每项说明根因、修改和新增/更新测试。不要顺手重构或处理 P2/P3，除非它们阻塞 P0/P1 修复。
```

## 9. Luna Review 子 agent 提示词

```text
你是独立代码 Review agent，模型为 gpt-5.6-luna。只审查，不编辑文件。审查第 {round_no}/30 轮新策略是否严格符合 {round_design_path} 和固定实验契约。

必须检查：
- 是否真的新增策略，是否修改或改变了已有策略行为；
- 算法、公式、边界和配置是否与设计一致；
- signal date、数据 shift、调仓日、成交日、估值日是否因果一致；
- PIT Universe、上市/退市、缺失数据、复权和幸存者偏差；
- 交易费用、滑点、容量、最小佣金、手数、现金和残单处理；
- NaN/inf、短样本、常数序列、空 Universe、并列排名和确定性；
- 是否改变公共 Runner 或比较契约，从而让收益不可公平比较；
- 测试是否验证关键机制和失败路径，是否存在只测 mock、不测真实接口的问题；
- 是否有过度拟合式硬编码、确认区间条件或数据窥探；
- diff 是否包含无关修改。

严重级别：
- P0：严重前视/数据污染、资金或数据破坏、回测结论整体无效。
- P1：显著扭曲收益风险、违反不可变约束、核心算法错误或关键测试缺失。
- P2：局部正确性、可维护性或非核心边界问题。
- P3：建议性改进。

每个发现必须给出可复现证据、精确文件与行号、影响和最小修复建议。不要把风格偏好升级为 P1。

输出严格 JSON：
{
  "round": {round_no},
  "verdict": "PASS|CHANGES_REQUIRED",
  "p0_count": 0,
  "p1_count": 0,
  "findings": [
    {
      "severity": "P0|P1|P2|P3",
      "title": "...",
      "evidence": "...",
      "file": "...",
      "line": 0,
      "impact": "...",
      "minimal_fix": "..."
    }
  ],
  "tests_reviewed": ["..."],
  "residual_risks": ["..."]
}

只有没有 P0/P1 时 verdict 才能为 PASS。即使 PASS，也保留真实 P2/P3 和 residual_risks。
```

## 10. 回测提交与等待提示词

```text
代码和测试已通过，开始第 {round_no} 轮可比回测。

1. 通过 GET /stockpred/fund-rotation/strategies 及详情接口确认 Champion 与 Challenger 均已注册，并读取服务端 default_config；不得在客户端猜默认值。
2. 构造一个只包含 Champion 与 Challenger 的 StrategyBatchRequest：schema_version="1"、mode="RESEARCH_ONLY"、固定研究选择起止日期、冻结 execution、唯一且可重试的 idempotency_key。
3. 保存原始请求到 backtest_request.json，再调用 POST /stockpred/fund-rotation/strategy-batches。
4. 保存 batch_id。使用 SSE 或 GET /stockpred/fund-rotation/strategy-batches/{batch_id} 持续等待，直到 SUCCEEDED、PARTIAL_SUCCEEDED、FAILED、CANCELED、STALLED 等终态。HTTP 202、后台进程存在或收到首个事件都不表示完成。
5. 对临时网络错误做有限退避重试；重试必须复用同一 idempotency_key，禁止重复创建实验。
6. 终态后校验批次 manifest、子 run manifest/checksum、quality_status、partial、publishable_for_comparison、snapshot fingerprint、framework/execution hash 和 fold 可比性。
7. 任一候选技术失败、质量门禁失败或身份不一致时，不做绩效排名，记录 TECHNICAL_OR_COMPARABILITY_FAILURE，Champion 不变。
8. 只有可比时才执行预注册 Champion 替换门禁，并把原始指标、差值和逐条 gate 结果写入 backtest_result.json 与 decision.json。
```

## 11. 验收标准

- 恰好记录 30 轮，每轮都有终态；失败轮也计数。
- 任何成为 Champion 的候选都通过 P0/P1、测试、可比性和绩效六项门禁。
- 现有策略实现没有被修改。
- 所有历史尝试均可追溯，不能只看到最后赢家。
- 确认区间没有用于第 1—30 轮选优。
- 最终候选状态为 `FROZEN_RESEARCH_CANDIDATE`，同时存在 104 周 forward shadow 预注册文件。
- 报告明确说明首轮 Universe 未验证、历史回测的多重尝试风险和剩余不确定性。
