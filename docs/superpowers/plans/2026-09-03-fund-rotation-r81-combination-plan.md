# R81 组合策略研究执行计划

> 目标：严格按 `optimizing-fund-rotation` 的 Champion-Challenger、冻结快照、滚动折叠和审计台账方法，先验证 R81 动态代表选择与 R58/R57 三因子信号组合，再按正交性评估后续候选。

## 执行约束

- 工作目录：`E:\code\stock\Vibe-Trading`；保持当前分支和用户已有改动，不创建或修改公共执行合同。
- 正式快照：`fund.lance` v77、`fact_fund_adj.lance` v671、`dim_fund.lance` v62，指纹 `7596807626fdf7f1aa9bdaddd84cd4575e15ac473c8331879d841ecacd941de6`。
- 正式研究区间：`2004-01-02..2022-07-29`；确认区间 `2022-08-01..2026-08-01` 只记录为已消费，不用于本轮选择。
- 折叠：Train156 / Validation52 / Test52 / Step52，共 14 折；连续账户运行，按折叠切分指标。
- 执行合同：初始资金 100 万、佣金 0.00025 最低 5、其他费率 0、参与率 0.05、ADV 20/10、滑点 5–30 bps、整手 100。
- 固定 seed：`e33b00bd5689`。找不到该身份时标记 `SEED_UNREPRODUCIBLE`，不得用 `f988e415122d` 替代正式证据。
- 新策略 ID：`ai_rotation_r82_economic_role_dynamic_rep_r57_signal`；独立包、独立 source snapshot 和 implementation hash。
- 只允许研究候选，不部署、不实盘、不修改已有 R81/R58/R57 行为。

## 任务 1：冻结实验合同和台账骨架

1. 建立 `experiments/fund_rotation_r81_combinations_20260903/`，写入中文 `experiment_spec.json`、`fold_manifest.json`、`ledger.jsonl` 初始记录和数据快照清单。
2. 生成 `seed_repro_probe`：尝试解析默认 `correlation_representative` seed，记录解析结果、身份、快照、失败原因；旧 `f988` 只写入背景引用字段。
3. 生成 R81 anchor 的正式运行请求，不使用已消费确认区间。

验证：JSON 可解析；快照版本/指纹、研究区间、14 折边界、执行合同和 seed 缺口均与设计规范一致；ledger 首条记录为 append-only 的 seed probe。

## 任务 2：先写 R82 失败测试

新增 `agent/tests/fund_rotation/test_ai_rotation_r82_economic_role_dynamic_rep_r57_signal.py`，先覆盖以下可观察行为：

1. descriptor、策略 ID、策略包路径和 implementation identity 独立于 R81/R58。
2. R81 的角色分类、动态代表排序、26 周锁定/刷新、硬失败重选和 PIT 结果在相同输入下保持一致。
3. 每个角色代表只使用信号日及之前的 49 日数据；R57 三因子权重固定 0.3/0.3/0.4，完整候选不足 2 个时 fail closed。
4. 改变信号日之后的未来行不会改变历史分数；代表硬失败后必须对新代表重新计算因子。
5. 角色 Top3、1/3 权重、现金、R34 staged reentry、R39 incumbent carry、R76 固定短债与 R81 一致。
6. 非代表角色成员变化不能改变当前代表的 R57 分数；输出含角色/代表/因子和 fail-closed 证据，不伪造 `cluster_id`。
7. 严格 JSON 决策证据和 Runner/PIT 合同通过。

验证：先运行该单文件，确认新行为测试按预期失败且失败原因对应缺失 R82 实现；不得先写生产实现。

## 任务 3：实现 R82 最小独立策略

1. 新建 `agent/backtest/fund_rotation/strategies/ai_rotation_r82_economic_role_dynamic_rep_r57_signal/`，仅复用纯函数、角色分类和既有 R34/R39/R76 逻辑，不继承 R58 的相关性聚类状态。
2. 在独立 session 中固定 R57 参数和 fail-closed 语义；保持 R81 的上游代表选择不可被 R57 反向改变。
3. 添加 registry 白名单、策略包导出和 descriptor；不修改公共 Runner、执行参数、旧策略或共享身份计算。
4. 为新包补充必要的 parity helper；若复制 R81 评估代码，测试必须证明代表选择和生命周期等价，避免隐式漂移。

验证：在实现 agent 的独立 review 后，运行 R82 单元测试、R57/R81 相关回归测试、registry 测试和 `git diff --check`；确认失败测试转绿且原有测试无回归。

## 任务 4：round_00 和 round_01 正式批次

1. 提交并轮询 `seed_repro_probe`，验证 202 状态不是终态，直到 `SUCCEEDED`、`FAILED` 或 `CANCELED`；保存完整 request/resolved/reports。
2. 在同一快照连续运行 R81 anchor，写入 `round_00` 基线审计产物；记录名义收益与成交完成率、父单成交率、阻塞率、换手率和容量。
3. 以 R81 anchor 为 Champion、R82 为 Challenger，创建 round_01 paired batch；只允许两个变体，幂等键稳定，schema `v1`、`RESEARCH_ONLY`。
4. 汇总 14 折 validation/test 指标，执行 Champion gate；无论通过与否都追加 ledger，不覆盖历史。

验证：两个策略使用同一 snapshot/universe/fold manifest/execution hash；批次为终态；报告身份无串线；折叠指标可由原始 equity 重建。

## 任务 5：逐轮研究循环（round_02..round_30）

每轮严格执行一次以下顺序：

1. 由 Sol analyst 读取当前 Champion、失败轮次、行为差异和执行诊断，只提出一个机制假设；若候选输入不可用或没有新增可证伪假设，写入 `NO_JUSTIFIED_HYPOTHESIS`。
2. 若需要实现，由全新 Luna implementer 先补失败测试，再添加新策略 ID；不得复用失败策略 ID 或改旧实现。
3. 由全新 Luna reviewer 按身份、PIT、因果性、执行合同、折叠门槛和最小改动审阅；最多 5 次修复循环。
4. 运行 paired batch，轮询终态，保存所有原始产物和 review package；严格按 gate 决定是否更新 Champion 指针。
5. 将假设、实现 hash、批次幂等键、门槛结果和下一轮理由追加到 ledger；失败策略不得删除。

候选顺序固定为 R82 → R77 防御相对动量（只有输入恢复可用才实施）→ R62 真逆波动率权重 → R74 角色级波动调整排序 → R75 总组合波动目标。若某方向改变层级、输入或执行合同，必须先停止并记录为不可比，而不是叠加到同一轮。

验证：最终存在 `round_01` 至 `round_30` 的完整记录；每个实际批次恰好两个变体；每轮都有终态、门槛判定和审阅证据；没有通过改名重复失败假设。

## 任务 6：冻结研究候选和最终审计

1. 对最后 Champion 重新计算全量研究指标、14 折汇总、执行质量、容量、回撤和收益归因；只读原始批次产物。
2. 运行独立的身份/快照/因果性/报告重建检查，确认结果不依赖确认区间或未来行。
3. 写入中文 `final_report.md`、`champion.json` 和 `forward_shadow_plan.json`，状态为 `FROZEN_RESEARCH_CANDIDATE` 或明确未通过。
4. 记录至少 104 周 forward-shadow 计划；不发起部署动作。

验证：完整验证命令输出、产物路径、最终状态和未解决限制均写入交付摘要；只有有新鲜命令证据时才声称“更优”。

## 子 agent 分工

- 研究设计/可行性报告已由两名只读 agent 完成并保存在 `.superpowers/`，后续不重复扫描。
- 任务 2–3 使用一个新的 Luna implementer；任务 3 完成后使用新的 Luna reviewer。若数量限制，按用户要求复用已完成的 agent，并在 ledger 记录复用原因。
- 任务 4–6 的批次编排和最终审计由主 agent 执行，避免多个 agent 同时写共享实验目录。
