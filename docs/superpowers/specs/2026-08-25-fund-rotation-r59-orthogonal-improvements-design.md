# R59 基金轮动正交改进与回测实验设计

> 状态：DRAFT / RESEARCH_ONLY  
> 日期：2026-08-25  
> 目标基线：`ai_rotation_r59_r39_signal_r57_positive_slope`  
> 适用仓库：`bird010/Vibe-Trading` / `data-layer-improve`

## 1. 目标

在不修改 R39、R57、R58、R59 既有策略实现，不修改公共 Runner、执行合同、PIT 数据语义和既有评价门禁的前提下，以 R59 为固定研究基线，设计一组**彼此尽量正交、可单独证伪、可独立回测**的策略增强。

本轮不追求一次性得到更漂亮的历史净值曲线，而是回答四个基础问题：

1. R59 目前高度集中在约 25 个交易日的短期趋势信息，加入**中期趋势**后是否存在稳定增益？
2. R59 固定 `1/top_n` 权重没有控制单个 ETF 的风险贡献，加入**真正的 inverse-volatility weighting** 后是否改善风险收益？
3. R59 周频重新排名可能把细小排序噪声变成交易，加入**rank hysteresis / exit buffer** 后是否能降低换手且不损害主要收益来源？
4. 当前“固定 K 聚类 → 锁定代表 → 排名”的复杂去重结构是否真的优于更直接的“排名 → 相关性约束”方法？

首阶段固定设计 5 个新策略：R60–R64。每个策略只验证一个主要机制；不得在首轮把多个 winner 直接拼在一起。

---

## 2. 与已有设计的关系

本设计是以下既有规范的增量扩展，不替代其研究纪律：

- `docs/superpowers/specs/2026-08-11-fund-rotation-signal-portfolio-risk-design.md`
- `docs/superpowers/specs/2026-08-19-ai-fund-rotation-optimization-loop-design.md`
- `docs/superpowers/specs/2026-08-25-r39-incremental-r57-parameter-campaign-design.md`

若存在冲突，以以下冻结约束为准：

1. 已经被查看、用于策略设计或参数选择的历史区间不得重新包装为 untouched OOS。
2. 新策略必须使用新 ID；不得通过修改旧策略代码覆盖历史含义。
3. 每次实验只改变一个主要策略机制。
4. 失败版本必须保留，并计入实验预算和 ledger。
5. 正式 Champion 晋级继续使用既有冻结 gates；本设计不得因某个新策略结果好看而修改门禁。
6. 公共 Runner、费用、滑点、成交容量、日历、PIT Universe、数据快照及执行语义必须保持一致。

特别说明：本文件把 R59 定义为**本轮机制研究基线**，不自动声称 R59 已经是正式 `champion.json` 中的 Champion。启动实验前必须读取当前实验账本和 `champion.json`。若正式 Champion 不是 R59，则：

- R59 ↔ R60–R64 的比较用于机制归因；
- 是否晋级仍需按既有实验契约与正式 Champion 比较。

---

## 3. 当前 R59 的事实基线

### 3.1 决策流水线

R59 当前核心流程为：

```text
PIT ETF Universe
    ↓
52 周周收益相关性
    ↓
固定 K=8 的层次聚类
    ↓
每簇锁定流动性代表 ETF
    ↓
49 行因果复权 OHLC
    ↓
R57 三因子：bias / slope / efficiency
    ↓
横截面 z-score：0.3 / 0.3 / 0.4
    ↓
额外要求 slope_25d > 0
    ↓
Top 3 代表簇
    ↓
固定 1/3 槽位
    ↓
新目标 50% staged re-entry
    ↓
释放权重按基础目标比例交给 incumbent
    ↓
最终 target weights + cash
```

### 3.2 R57 三个信号的实际信息尺度

R57 当前三个因子都主要来自约 25 个交易日的价格路径：

- `bias`：25 日均线乖离序列的 25 日回归斜率；
- `slope`：25 日归一化价格 OLS 斜率乘 R²；
- `efficiency`：25 日路径方向收益乘效率比率。

综合分：

\[
S^{R57}_{i,t}
=0.3z(Bias_{25D})
+0.3z(Slope_{25D})
+0.4z(Efficiency_{25D})
\]

R59 在综合评分后又要求：

\[
Slope_{25D}>0
\]

当前字段名 `raw_slope_25d` 实际来自 R57 的 `slope` 值；它并非未乘 R² 的 raw OLS slope。由于 \(R^2\ge0\)，只用于判断正负时符号等价，但命名应在新策略中避免继续传播误导。

### 3.3 主要结构缺口

当前结构可简化为：

```text
Short Trend
+ Short Trend
+ Short Trend
+ Short Trend Gate
+ Equal Weight
```

缺少三个相对独立的维度：

```text
Medium-Term Trend
Portfolio Risk Allocation
Turnover Hysteresis
```

此外，聚类本身是实现“避免重复风险暴露”的手段，而不是投资目标。必须存在一个更简单的结构性 challenger 来回答：复杂聚类是否真的创造净价值。

---

## 4. 第一性原理与设计原则

### 4.1 真正要解决的问题

基金轮动长期可行的必要条件是：

\[
Predictive\ Benefit + Risk\ Improvement
>
Estimation\ Error + Trading\ Cost + Model\ Risk
\]

因此策略应拆成三个问题：

```text
Signal：买谁？
Risk：买多少？
Execution：什么时候值得换？
```

R60/R61 只研究 Signal；R62 只研究 Risk；R63 只研究 Execution；R64 研究 Diversification Architecture。

### 4.2 不允许的做法

首阶段禁止：

- 同时加入 126D 趋势、inverse-vol、rank buffer 和市场状态过滤；
- 直接加入 Value、Quality、Macro、Random Forest、HMM、XGBoost；
- 大范围 grid search；
- 根据全历史最优 Sharpe 选择窗口；
- 修改 R59 原实现以复用新逻辑；
- 因新策略需要更多数据而偷偷改变 PIT Universe；
- 把 risk scaling 带来的低回撤称为 Alpha；
- 在结果出来后调整 gate、费用、滑点或 benchmark。

### 4.3 研究优先级

首阶段优先级固定为：

1. R60：中期绝对趋势门禁；
2. R61：短中期双尺度评分；
3. R62：真正的 inverse-volatility weighting；
4. R63：Top-N / Top-(N+1) 排名迟滞；
5. R64：去聚类的相关性约束 challenger。

该顺序仅表示实现/研究优先级，不表示结果预期排序。

---

## 5. 统一不变量

除对应策略章节明确声明的单一变化外，R60–R63 必须逐值继承 R59 的以下行为：

- 周频 signal schedule；
- PIT Universe；
- 历史资格检查；
- signal-date market eligibility；
- 52 周相关性窗口；
- K=8 聚类；
- 26 周重聚类；
- cluster quality gates；
- representative lock 与硬失效替换；
- R57 `bias/slope/efficiency` 公式；
- R57 因子权重 `0.3/0.3/0.4`；
- `top_n=3`；
- staged re-entry fraction `0.5`；
- incumbent carry；
- signal information cutoff=`CLOSE`；
- 下一执行阶段、费用、滑点、容量与 lot size；
- quality status 规则；
- 既有工件与 decision trace 语义。

R64 是结构性 challenger，只有聚类/代表链路被明确替换，其余 PIT、信号、Top3、staging、carry、执行和评价语义继续保持一致。

所有诊断输出必须满足 strict JSON：

- mapping key 必须为字符串；
- 禁止 NaN/Inf；
- 不可用数值写 `null`；
- reason code 和 status 必须稳定、可测试。

---

## 6. 实验总览

| 策略 | 主要假设 | 相对 R59 唯一主要变化 | 初始冻结参数 |
|---|---|---|---|
| R60 | 短期反弹只有得到中期趋势确认才更可靠 | 新增 126D absolute momentum gate | `return_126d > 0` |
| R61 | 多时间尺度比三个同源 25D 特征更稳健 | 排名中加入 126D momentum | short/medium `0.5/0.5` |
| R62 | 名义等权导致高波动 ETF 主导风险 | Top3 槽位改为 true inverse vol | 60D realized vol，单 ETF 50% cap |
| R63 | Top3/Top4 微小换位主要是排名噪声 | incumbent 位于 Top4 时保留 | exit buffer=1 |
| R64 | 固定 K 聚类可能比问题本身更复杂 | 去掉 clustering，改为 greedy corr constraint | 52W corr，threshold=0.80 |

所有参数都是首版预注册值，不代表历史最优值。

---

# 7. R60：中期绝对趋势门禁

## 7.1 策略 ID

```text
ai_rotation_r60_r59_medium_trend_gate
```

建议显示名：

```text
R59 + 126日中期正趋势门禁
```

## 7.2 假设

R59 的 25 日正斜率可以捕捉短期反弹，但不能区分：

```text
中期上升趋势中的短期继续上涨
```

与：

```text
中期下跌趋势中的一个月反弹
```

R60 验证：要求 126 个交易日累计收益也为正，是否能减少假突破和熊市反弹中的错误入场。

## 7.3 算法

使用截至 signal-day close 的因果复权收盘价。定义：

\[
M_{126D}(i,t)
=
\frac{P^{adj}_{i,t}}{P^{adj}_{i,t-126}}-1
\]

需要 127 个有效复权 close observations。

R60 的候选资格：

\[
Eligible^{R60}_{i,t}
=
Complete^{R57}_{i,t}
\land
Slope_{25D}(i,t)>0
\land
M_{126D}(i,t)>0
\]

排序仍完全使用 R57 原综合分：

\[
RankScore^{R60}=S^{R57}
\]

即：**126D 只做 gate，不参与排序。**

## 7.4 数据获取

R59 的 49 行窗口继续用于 R57 三因子；另取最大 127 行因果 OHLC + adjustment data 用于 126D return。

不允许：

- 使用 signal date 之后的 adj factor；
- 用未复权 close 与复权 R57 因子混用；
- 因某 ETF 不足 127 行就用更短窗口代替；
- 用 calendar days 替代交易日 observations。

当前 52 周聚类 warmup 已大于 127 个交易日，因此首版原则上不需要扩大 Runner 的公共 warmup；但策略内部 `daily_bars` / `fund_adjustments` lookback 必须显式覆盖 127 行。

## 7.5 数据不足行为

若代表 ETF：

- R57 三因子完整，但 126D 数据不足：该 ETF 不通过 R60 gate；
- 126D return 非有限：不通过 gate；
- 复权链路失败：不通过 gate，并记录具体 status；
- gate 后完整候选少于 2：沿用 R59 的 `INSUFFICIENT_COMPLETE_CANDIDATES` 语义，但 diagnostics 必须进一步区分是 R57 incomplete 还是 medium-trend filtered。

不得把数据不足解释为负趋势。

## 7.6 Diagnostics

每个 factor row 至少新增：

```text
medium_return_126d
medium_return_observations
medium_return_required_observations = 127
medium_return_status
medium_trend_positive
r57_complete_candidate
r59_positive_slope_candidate
r60_medium_trend_candidate
```

`score_details` 新增：

```text
r59_candidates_before_medium_gate
medium_trend_qualified_candidates
medium_trend_rule = "adjusted_return_126d > 0"
```

## 7.7 单元测试

至少覆盖：

1. 127 行上涨价格 → `return_126d > 0`；
2. 127 行下跌价格 → gate 拒绝；
3. 只有 126 行 → `INSUFFICIENT_OBSERVATIONS`；
4. signal date 后追加极端价格，不改变当期结果；
5. signal date 后追加 adj factor，不改变当期结果；
6. 25D slope>0、126D return<0 → 拒绝；
7. 25D slope>0、126D return>0 → 保留；
8. 中期 gate 不改变 R57 composite 数值和排序；
9. R59 未被修改，原 R59 测试逐值通过；
10. diagnostics 无 NaN/Inf。

## 7.8 首轮成败解释

若 R60 改善 Sharpe/MDD 且在多个 fold 方向一致，说明“短期趋势需要中期确认”具有证据。

若仅 CAGR 上升但回撤/换手恶化，不能直接判定通过。

若只有 126D 单点显著好、而后续 63D/252D 邻域完全失败，应怀疑参数偶然性。

---

# 8. R61：短中期双尺度评分

## 8.1 策略 ID

```text
ai_rotation_r61_r59_dual_horizon_score
```

建议显示名：

```text
R59 短中期双尺度趋势评分
```

## 8.2 假设

R59 的三个 R57 因子虽然公式不同，但都集中在约 25D，存在信息冗余。R61 不新增新的基本面/宏观特征，只加入一个独立时间尺度：126D price momentum。

R61 验证的是：

> 在保留 R59 25D 正趋势 gate 的前提下，让排序同时参考 25D 路径质量和 126D 中期趋势，是否比单尺度短期评分更稳健。

注意：R61 **不继承 R60 的 `126D > 0` gate**。否则会同时改变 gate 和 ranking，无法单独归因。

## 8.3 算法

第一步：按 R57 原逻辑得到：

\[
S^{short}_{i,t}=S^{R57}_{i,t}
\]

第二步：对具有完整 R57 分数且具有 127 行复权 close 的候选，计算：

\[
M^{medium}_{i,t}=M_{126D}(i,t)
\]

第三步：在同一完整候选截面分别标准化：

\[
z^{short}_{i,t}=z(S^{short}_{i,t})
\]

\[
z^{medium}_{i,t}=z(M^{medium}_{i,t})
\]

第四步：等权融合：

\[
S^{R61}_{i,t}
=0.5z^{short}_{i,t}+0.5z^{medium}_{i,t}
\]

第五步：仍沿用 R59 正斜率门禁：

\[
Slope_{25D}>0
\]

最后按 `S_R61` 选 Top3。

### 为什么重新标准化 short composite

R57 composite 虽由 z-score 加权得到，但其横截面标准差并不保证等于 1。若直接与 `z(126D)` 相加，实际权重不一定是 50/50。R61 因此先对 R57 composite 再做一次截面 z-score，确保两个尺度的名义权重可解释。

若某一截面标准差接近 0，则该尺度 z-score 全部置 0，不能产生 NaN/Inf。

## 8.4 不变项

- R57 内部三因子权重仍为 0.3/0.3/0.4；
- 25D slope positive gate 仍保留；
- 不新增 126D absolute gate；
- Top3、Equal Weight、staging、carry 不变。

因此唯一主要变化是：**排名分数增加一个中期时间尺度。**

## 8.5 Diagnostics

每个 row 至少新增：

```text
r57_composite_score
r57_composite_zscore
medium_return_126d
medium_return_zscore
dual_horizon_score
dual_horizon_rank
```

`score_model`：

```json
{
  "id": "r61_dual_horizon_trend",
  "version": "1",
  "components": {
    "short_r57_composite_z": 0.5,
    "medium_return_126d_z": 0.5
  }
}
```

## 8.6 单元测试

至少覆盖：

1. 126D 强趋势可以改变两个 short score 接近 ETF 的相对排序；
2. short score 明显领先时，中期项不应无条件覆盖；
3. 126D 数据不完整的 ETF 不进入 dual-horizon complete set；
4. short composite 截面零方差时 z=0；
5. medium return 截面零方差时 z=0；
6. 25D slope<=0 即使 dual score 第一也必须过滤；
7. R61 不执行 R60 的 `return_126d > 0` gate；
8. signal date 后数据变化不影响结果；
9. diagnostics JSON 合法且排序确定性；
10. 相同输入重复执行得到完全相同的 target weights。

---

# 9. R62：真正的 Inverse-Volatility Weighting

## 9.1 策略 ID

```text
ai_rotation_r62_r59_true_invvol
```

建议显示名：

```text
R59 Top3 真逆波动风险权重
```

## 9.2 假设

R59 的 Top3 每个槽位固定 `1/3`：

\[
w_i=1/3
\]

这等价于名义资金等权，而不是风险等权。若一个 ETF 的波动率是另一个的 2–3 倍，它会主导组合回撤。

仓库已有 `ai_rotation_r29_invvol_slots`，但其缩放因子是：

\[
\frac{1}{1+\sigma_i}
\]

当周收益波动率只有几个百分点时，不同 ETF 的 factor 非常接近 1，实际权重仍接近等权。因此 R29 不能视为对传统 true inverse-vol 的充分验证。

R62 专门验证：**保持 R59 买谁完全不变，只改变 Top3 之间的资金分配。**

## 9.3 波动率定义

对 R59 已选中的有效代表 ETF，取截至 signal close 的 61 个复权 close，形成 60 个日收益：

\[
r_d=\frac{P_d}{P_{d-1}}-1
\]

定义年化 realized volatility：

\[
\sigma_i=Std(r_{i,1:60},ddof=0)\sqrt{252}
\]

首版冻结窗口：60 个日收益。

数值安全：

```text
sigma <= 1e-8 或非有限 → volatility INVALID
```

该阈值仅作为数值有效性保护，不作为调参变量。

## 9.4 槽位总敞口保持不变

R62 不允许因为 weighting 自行改变 R59 的基础现金含义。

若当前有 `m` 个 filled slots：

\[
E_{base}=m/top_n
\]

例如：

- 3 个 filled → 总基础敞口 1.0；
- 2 个 filled → 总基础敞口 2/3；
- 1 个 filled → 总基础敞口 1/3。

inverse-vol 只在 filled representatives 之间分配 `E_base`：

\[
q_i=1/\sigma_i
\]

\[
w_i^{raw}=E_{base}\frac{q_i}{\sum_j q_j}
\]

## 9.5 单 ETF 50% 上限

首版加入固定安全上限：

\[
w_i\le0.50
\]

使用确定性的 capped renormalization / water-filling：

1. 先按 inverse vol 计算 raw weights；
2. 超过 0.50 的 ETF 固定在 0.50；
3. 剩余未分配权重继续按未封顶 ETF 的 inverse-vol 比例分配；
4. 重复直到全部满足 cap 或无剩余 ETF；
5. 因不可满足而剩余的敞口留现金，不允许突破 cap。

不得按代码顺序随意截断后简单 renormalize，因为会产生顺序依赖。

## 9.6 数据不足 fallback

为单独隔离 weighting 效果，若**任一 filled representative** 无法形成完整 60D volatility：

- 本期整个 weighting layer 回退到 R59 原 `1/top_n` 槽位；
- 不因为风险数据不足删除该 ETF；
- diagnostics 记录 `fallback_reason`；
- 不得部分 ETF 用 inverse-vol、部分 ETF 用 equal weight。

这样避免把“更长数据要求导致 Universe 变化”误认为 weighting 收益。

## 9.7 与 staged re-entry / carry 的顺序

顺序固定：

```text
R59 selected clusters
→ true inverse-vol base slot weights
→ staged re-entry
→ incumbent carry
→ final target weights / cash
```

不能在 carry 后再次 inverse-vol，否则会改变 R39 carry 的经济含义并形成两个同时变化的机制。

## 9.8 Diagnostics

新增：

```text
portfolio_weighting.mode = "true_inverse_volatility"
portfolio_weighting.window_returns = 60
portfolio_weighting.annualized_volatility
portfolio_weighting.inverse_vol_score
portfolio_weighting.base_exposure
portfolio_weighting.pre_cap_weights
portfolio_weighting.post_cap_weights
portfolio_weighting.max_etf_weight = 0.50
portfolio_weighting.fallback_reason
```

## 9.9 单元测试

至少覆盖：

1. vol 10% 与 20% 时，未触发 cap 前权重大致为 2:1；
2. 与 R29 `1/(1+sigma)` 不同，确保真正使用 `1/sigma`；
3. 三个 filled slots 权重合计 1；
4. 两个 filled slots 权重合计 2/3，剩余 1/3 现金；
5. 50% cap 被严格满足；
6. cap 后重新分配无顺序依赖；
7. 任一 selected ETF volatility 不完整 → 整体回退 R59 Equal Slot；
8. weighting 不改变 selected codes；
9. weighting 在 staged re-entry 之前执行；
10. signal date 后价格不影响 volatility；
11. 零/非有限 volatility 不产生无限权重；
12. 最终 target weights + cash 不超过 1，且无负数。

---

# 10. R63：Rank Buffer / Hysteresis

## 10.1 策略 ID

```text
ai_rotation_r63_r59_rank_buffer
```

建议显示名：

```text
R59 Top3 入场 Top4 退出排名缓冲
```

## 10.2 假设

周频横截面排名中，第 3 与第 4 名的微小变化通常不足以证明预期收益发生实质改变，但 R59 会产生真实卖出/买入。

R63 验证：

```text
Entry：必须进入 Top3
Exit：原持仓跌出 Top4 才因排名原因退出
```

是否能减少排名噪声导致的换手，同时保留趋势恶化时的快速退出。

## 10.3 资格与强制退出

Rank Buffer 不能保护已经失效的标的。

一个 incumbent cluster 只有同时满足以下条件才允许被 buffer 保留：

1. 仍属于当前 clustering epoch；
2. 当前 representative 有效；
3. PIT market eligible；
4. R57 三因子完整；
5. R59 `Slope_25D > 0` gate 仍通过；
6. 当前 filtered ranking 中 rank <= 4。

以下情况立即退出，不受 buffer 保护：

```text
PIT eligibility failure
hard tradability/liquidity failure
representative unavailable
factor incomplete
positive-slope gate failure
recluster epoch reset
rank > 4
```

## 10.4 选择算法

首版固定：

```text
TOP_N = 3
EXIT_BUFFER = 1
EXIT_RANK = 4
```

算法：

```python
if reclustered:
    retained = []
else:
    retained = previous_selected_clusters that are still valid and rank <= 4

fillers = ranked_current_clusters excluding retained
selected = (retained + fillers)[:3]
```

`retained` 自身按当前 rank 排序，而不是按上周顺序。

## 10.5 状态语义

R63 新增 session state：

```text
_previous_selected_clusters
```

这里保存的是**上期基础 slot selection**，不是最终有权重的代码集合。

原因：

- staged re-entry 会改变最终权重大小；
- incumbent carry 会放大持续目标；
- buffer 应针对“策略想持有哪些 cluster”，而不是针对某个权重是否大于 0。

每次 recluster 必须重置 `_previous_selected_clusters`。Cluster label 不能跨 epoch 继承身份。

若某簇仍被选择，但 representative 因硬失效更换，cluster 可以保留；新的 representative 是否 staged re-entry 继续按 R39/R59 原规则处理。

## 10.6 不使用 1.5 倍乘法阈值

R57 旧 Top1 策略存在：

\[
Score_{challenger} > 1.5\times Score_{held}
\]

对于可为负值的 z-score composite，乘法阈值存在符号问题。因此 R63 不复制该规则，只测试 rank hysteresis。

后续若要研究 score gap，应使用：

\[
Score_{challenger}-Score_{incumbent}>\delta
\]

并单独设计，不与 R63 首版混合。

## 10.7 Diagnostics

新增：

```text
rank_buffer.entry_rank = 3
rank_buffer.exit_rank = 4
rank_buffer.previous_selected_clusters
rank_buffer.retained_clusters
rank_buffer.forced_exit_clusters
rank_buffer.current_rank_by_cluster
rank_buffer.epoch_reset
```

对每个候选记录：

```text
buffer_eligible
buffer_retained
buffer_forced_exit_reason
```

## 10.8 单元测试

至少覆盖：

1. 上期第3、本期第4、正趋势 → 保留；
2. 上期第3、本期第5 → 退出；
3. 上期第3、本期第4、slope<=0 → 强制退出；
4. 上期第3、本期 factor incomplete → 强制退出；
5. recluster → buffer 清零；
6. 同 epoch representative 替换时 cluster identity 仍可保留；
7. retained + fillers 最多 3 个；
8. retained 顺序按本期 rank；
9. buffer 不改变 composite score；
10. staging/carry 行为继续与 R59 一致；
11. deterministic replay 一致。

---

# 11. R64：去聚类的直接相关性约束 Challenger

## 11.1 策略 ID

```text
ai_rotation_r64_direct_corr_diversification
```

建议显示名：

```text
R59 信号直接相关性约束 ETF 轮动
```

## 11.2 目标

R64 不是“小参数优化”，而是一个结构性 challenger，用于验证：

> “固定 K 聚类 + 代表 ETF”是否是实现风险去重的必要结构。

投资目标是避免同时持有高度重复风险暴露，而不是正确识别出市场恰好存在 8 个稳定簇。

R64 用更简单的流程替代：

```text
全部 PIT eligible ETF
→ R57 score
→ positive slope gate
→ 按 score 排名
→ greedy pairwise correlation constraint
→ Top3
```

## 11.3 新配置

R64 不应复用 `CorrelationRepresentativeConfig` 中无意义的 `k`、cluster gate、representative candidate 参数。

新增独立 frozen Pydantic config，例如：

```python
class DirectCorrelationDiversificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    top_n: int = 3
    correlation_lookback_weeks: int = 52
    min_pairwise_weeks: int = 20
```

首版 correlation threshold 不暴露为可随请求任意调节的参数，而在策略模块冻结：

```text
MAX_PAIRWISE_CORRELATION = 0.80
```

这样首轮只有一个预注册结构版本。后续若进行敏感性实验，必须用新策略 ID。

## 11.4 因子截面

R64 不再只对每个 cluster representative 计算 R57 因子，而是对所有当前 PIT eligible ETF 计算相同的：

```text
bias_25d
slope_25d
efficiency_25d
R57 composite
positive slope gate
```

这意味着 R57 z-score 的截面从“代表 ETF 集合”变为“全部 eligible ETF”。这是移除聚类后的必要后果，必须在报告中明确，不应宣称只改了一个数值参数。

R64 的主要机制定义为：

> 用直接 pairwise diversification 替代 clustering + representative abstraction。

## 11.5 Pairwise correlation

使用与当前聚类相同来源的因果周收益窗口：

```text
frequency = weekly
lookback = 52 weeks
minimum pairwise observations = 20
```

对候选 `i` 与已选 ETF `j`：

\[
Corr(i,j)<0.80
\]

才允许加入。

这里使用普通相关系数而不是绝对相关系数：负相关是有价值的分散化，不应因为 `abs(corr)` 大而被排除。

## 11.6 Greedy Selection

```python
selected = []

for code in ranked_codes:
    if len(selected) == 3:
        break

    if every pairwise corr(code, held) is finite and < 0.80:
        selected.append(code)
```

第一只候选无需 pairwise check。

若与任一已选 ETF 的 pairwise correlation 因数据不足而不可计算：

- 首版保守地跳过该候选；
- 记录 `PAIRWISE_CORRELATION_UNAVAILABLE`；
- 禁止把 NaN 当 0；
- 禁止把“未知相关性”解释为“低相关”。

若最终不足 3 只，空余 slot 留现金，仍按每 slot `1/3` 的基础语义处理。

## 11.7 不再适用的 cluster 逻辑

R64 明确不运行：

```text
hierarchical clustering
K=8
MAX_CLUSTER_SHARE gate
EFFECTIVE_CLUSTER_COUNT gate
medoid selection
representative_min_cluster_corr
representative lock
recluster_interval
```

因此不得继续输出虚假的 cluster quality status。

R64 的 `quality_status` 只反映它自身真实的数据/策略质量，例如：

- PIT eligibility 是否有效；
- R57 factor coverage；
- correlation coverage；
- 是否有足够可比较候选。

首版不要为了 UI 对齐伪造 cluster artifacts。

## 11.8 组合生命周期

R64 仍保留：

```text
Top3 fixed slots
→ staged re-entry 50%
→ incumbent carry
```

但 incumbent identity 从“cluster representative code”变为直接 ETF code。

如果 ETF 本期仍被选中，则视为 incumbent；否则为退出/新入场。

## 11.9 Artifacts / Diagnostics

为避免首版修改公共 artifact publisher，不新增必须注册的公共 artifact role。使用现有：

```text
factor_scores
exclusions
decisions
```

并在 decision diagnostics 中保存：

```text
ranked_codes
selected_codes
correlation_lookback_weeks
min_pairwise_weeks
max_pairwise_correlation
selection_pairwise_correlations
correlation_rejected_candidates
correlation_unavailable_candidates
```

所有 correlation mapping key 使用字符串，例如：

```text
"510300.SH|159915.SZ": 0.73
```

禁止 tuple key 和 NaN。

## 11.10 单元测试

至少覆盖：

1. 排名第1无条件作为第一候选；
2. 第2与第1 corr=0.79 → 可选；
3. corr=0.80 → 按严格 `<0.80` 规则拒绝；
4. corr=0.81 → 拒绝；
5. corr=-0.90 → 可选；
6. corr=NaN / 有效周不足20 → 保守跳过；
7. 只找到2个合格 ETF → 2/3 exposure + 1/3 base cash；
8. 不执行 clustering/gates/representative selection；
9. R57 因子公式与 R59 一致；
10. positive slope gate 与 R59 一致；
11. staged re-entry / incumbent carry 保持；
12. signal date 后周收益不影响 correlation；
13. diagnostics 无非字符串 key 和非有限数值。

---

# 12. 推荐代码布局

首版按策略隔离，不重构旧策略：

```text
agent/backtest/fund_rotation/strategies/
  ai_rotation_r60_r59_medium_trend_gate/
    __init__.py
    strategy.py
  ai_rotation_r61_r59_dual_horizon_score/
    __init__.py
    strategy.py
  ai_rotation_r62_r59_true_invvol/
    __init__.py
    strategy.py
  ai_rotation_r63_r59_rank_buffer/
    __init__.py
    strategy.py
  ai_rotation_r64_direct_corr_diversification/
    __init__.py
    config.py
    strategy.py
```

对应测试：

```text
agent/tests/fund_rotation/
  test_ai_rotation_r60_r59_medium_trend_gate.py
  test_ai_rotation_r61_r59_dual_horizon_score.py
  test_ai_rotation_r62_r59_true_invvol.py
  test_ai_rotation_r63_r59_rank_buffer.py
  test_ai_rotation_r64_direct_corr_diversification.py
```

注册：

```text
agent/backtest/fund_rotation/strategies/registry.py
```

只允许对 `registry.py` 做新增 import 和 whitelist entry，不重排/重命名旧策略。

## 12.1 为什么暂不抽共享 helper

R60–R63 与 R59 的 `evaluate()` 可能存在一定重复。首轮允许研究层有限复制，原因是：

- 避免为复用代码而重构 R59，导致基线 source identity 改变；
- 避免一个 shared helper 改动同时改变多个已跑策略；
- 让每个 challenger 的真实变化范围可独立审查。

只有当某一机制通过研究门禁、准备长期保留时，再单独设计共享组件重构，并要求“重构前后逐值 parity”测试。

---

# 13. StrategyDataRequirements

R60/R61/R62/R63 继续要求：

```text
fund
fact_fund_adj
dim_fund
```

字段至少保持当前 R59/R57 所需：

```text
ts_code
trade_date
name
list_date
open
high
low
close
pre_close
vol
amount
adj_factor
```

### R60/R61

内部额外需要最多 127 行 OHLC/adjustment，但当前 52 周 correlation warmup 已更长，因此公共 `warmup_trade_days` 不应为了该变体而降低或改变。

### R62

需要 61 行 close 形成 60 日收益，同样低于现有 52 周 warmup。

### R64

使用 52 周 weekly returns，与当前 correlation strategy 的时间长度一致；同时需要 R57 49 行 OHLC。

如果实际 `resolve_requirements()` 无法表达不同用途的多个内部 lookback，保持当前公共 warmup 的保守上界，不为减少 warmup 做优化。

---

# 14. 实现顺序

每个策略必须独立完成：

```text
Design frozen
→ failing unit tests
→ minimal implementation
→ independent review
→ P0/P1 clear
→ target tests
→ fund-rotation regression tests
→ paired backtest
→ ledger + decision
```

严格顺序：

```text
R60 → R61 → R62 → R63 → R64
```

一个策略失败不阻止进入下一个机制，但失败必须记录，不得偷偷修改后用同一个 ID 重跑。

如果失败原因是实现 bug，可在同一策略 ID 的实现阶段修复，直到第一次正式回测；一旦该策略已产生用于研究判断的正式回测结果，任何行为变化都必须使用新策略 ID。

---

# 15. 回测比较契约

## 15.1 固定比较身份

每个正式比较必须验证：

```text
data_snapshot_fingerprint identical
calendar identical
PIT universe policy identical
execution config hash identical
commission identical
slippage identical
capacity settings identical
initial capital identical
benchmark/evaluation policy identical
```

只有策略代码和该 challenger 声明的主要机制允许不同。

## 15.2 R59 机制对照

每个 R60–R64 的分析报告都必须包含一份相对 R59 的 matched comparison：

```text
same folds
same snapshot
same execution
same dates
```

如果当前 formal Champion 不是 R59，这个比较只用于机制归因，不自动触发 Champion promotion。

## 15.3 正式晋级

正式晋级继续使用既有冻结门禁，不在本设计重新发明指标：

- Validation Sharpe 严格高于正式 Champion；
- 年化收益不低；
- 最大回撤恶化不超过 1 个百分点；
- 超过半数有效 folds 的 Sharpe 获胜；
- 无 PIT、执行质量、完整性、可比性、前视偏差门禁失败。

若 R62 明显降低回撤但因收益略低而没有通过正式 Champion gate，报告可标记为：

```text
RISK_SHAPING_EVIDENCE_ONLY
```

但不得擅自放宽 promotion gate。

---

# 16. 统一评价指标

除现有 Champion gates 外，每个新策略报告必须至少包含：

## 16.1 收益

```text
CAGR
annualized_return
median_calendar_year_return
positive_year_ratio
```

## 16.2 风险

```text
daily_max_drawdown
annualized_volatility
Sharpe
Sortino
Calmar
worst_month_return
```

最大回撤必须来自日频净值，不得只用周末/季度采样节点。

## 16.3 执行

```text
annualized_one_way_turnover
trade_count
average_holding_days
median_holding_days
commission_cost
slippage_cost
total_execution_cost
```

## 16.4 资金使用

```text
average_cash_weight
p50_cash_weight
p90_cash_weight
average_gross_exposure
```

## 16.5 稳健性

```text
per_fold metrics
per_year metrics
bull / bear / sideways descriptive slices
parameter-neighborhood results when authorized
```

市场 regime 分片首阶段只用于**事后描述**，不能反馈修改当轮参数。

---

# 17. 交易成本压力测试

新策略只有在基础统一执行成本比较完成后，才进行附加 cost stress。

至少报告：

```text
base configured cost
5 bps equivalent stress
10 bps
20 bps
30 bps
```

具体实现应优先通过统一 ExecutionConfig/成本模型完成，不允许策略内部自行扣减一个简化费用。

重点观察：

\[
Net\ CAGR(c),\ Sharpe(c),\ Calmar(c)
\]

随成本 \(c\) 增加是否平滑恶化。

R63 的主要价值若来自降低 turnover，则必须在 cost stress 中体现；如果只有零成本回测更好，而较现实成本下无优势，则不支持该机制。

---

# 18. 参数敏感性：只在机制初步成立后执行

首版禁止同时跑全参数网格。

只有一个机制在冻结首版中显示值得继续研究时，才授权邻域参数；每个参数版本必须新 ID、进入实验预算，并继续遵守 selection/validation/OOS 边界。

建议邻域：

| 机制 | 首版 | 后续允许邻域 |
|---|---:|---|
| R60 medium gate | 126D | 63D / 126D / 252D |
| R61 medium score | 126D | 63D / 126D / 252D |
| R62 vol window | 60D | 40D / 60D / 120D |
| R63 exit buffer | +1 | +0 / +1 / +2 |
| R64 max corr | 0.80 | 0.70 / 0.80 / 0.90 |

判断标准不是寻找单一最优点，而是寻找参数平台：

```text
相邻合理参数大多方向一致
```

优于：

```text
只有一个参数点异常好
```

不得做以下 cross-product：

```text
3 个 momentum windows
× 3 个 vol windows
× 3 个 buffers
× 3 个 corr thresholds
```

---

# 19. Winner 组合规则

R60–R64 首轮必须独立回测。

只有某个机制满足以下条件，才进入组合候选：

1. 相对 R59 的机制增益可解释；
2. 不依赖单一 fold；
3. 实现与 PIT review 通过；
4. cost stress 后仍有意义；
5. 若做过邻域实验，不表现为尖峰参数；
6. 没有明显重复已有 winner 的同源信息。

组合时仍执行“一次只加一个模块”：

```text
best_signal
→ + best_risk_weighting
→ + best_execution_hysteresis
```

禁止一次性：

```text
R60 + R61 + R62 + R63 + R64
```

R65 及之后的 ID **不在本设计预先保留**；根据实验结果单独设计，避免在没有证据前预设组合路径。

---

# 20. 暂缓项目

以下方向有研究价值，但不进入 R60–R64 首阶段：

## 20.1 Portfolio Volatility Target

候选机制：

\[
Exposure_t
=
\min\left(1,\frac{\sigma^*}{\hat\sigma_{portfolio,t}}\right)
\]

它只缩放总风险敞口，不改变 Alpha 排名。应在 R62 风险权重结果出来后单独设计。

## 20.2 Defensive Asset 替代部分 Cash

空 slot 或 risk-off 时可研究短久期债券/现金类 ETF，但必须独立定义 safe asset PIT universe、趋势 gate、流动性和风险，不能默认“债券永远安全”。

## 20.3 Macro / Regime

不直接复用 `R32` 的 `510300.SH 四周收益 <= 0 → 全部现金` 作为 R59 全资产状态开关。若未来研究 regime，应至少区分：

```text
asset-own trend
market breadth
portfolio volatility
```

并保持 regime 与 ranking 分层。

## 20.4 ML

在当前独立市场周期数量有限的情况下，暂不优先增加 Random Forest/HMM/XGBoost/NN。复杂模型不会增加独立市场 regime 数量，反而扩大模型选择自由度。

---

# 21. 多重尝试与策略级过拟合控制

当前策略已经迭代到 R59，最大的研究风险之一已经从“单策略代码是否前视”升级为：

> 同一历史数据被几十个策略版本反复观察后，最终 winner 可能只是多重尝试中的幸运样本。

因此 R60–R64 必须：

1. 在实现前把假设、公式、窗口、权重、gate、失败条件写入 ledger；
2. 一个正式回测结果出现后禁止同 ID 改规则；
3. 所有失败结果保留；
4. 明确记录累计 strategy attempts；
5. 不把已消费区间称为 untouched OOS；
6. 最终候选仍需 forward shadow；
7. 若研究平台已支持 Deflated Sharpe Ratio / PBO，则把 R60–R64 纳入全历史策略尝试集合，而不是只对 winner 计算。

---

# 22. Review Checklist

每个策略进入回测前，Review agent 必须逐项确认：

## 22.1 通用

- [ ] 新策略使用唯一 ID。
- [ ] 没有修改 R39/R57/R58/R59 实现。
- [ ] 没有修改公共 Runner/Execution 语义。
- [ ] signal cutoff 保持 CLOSE。
- [ ] 所有 lookback 截止 signal date。
- [ ] adjustment data 不读取未来。
- [ ] diagnostics strict JSON。
- [ ] selected weights 非负且总和 + cash <= 1。
- [ ] deterministic replay。
- [ ] registry 显式注册。
- [ ] 原有基金轮动回归测试通过。

## 22.2 R60

- [ ] 126D 只做 gate，不进 ranking。
- [ ] 需要 127 个 close observations。
- [ ] 数据不足与负趋势分开记录。

## 22.3 R61

- [ ] 没有继承 R60 medium positive gate。
- [ ] short composite 和 medium return 独立标准化。
- [ ] 50/50 仅作用于 ranking。

## 22.4 R62

- [ ] 公式是 `1/sigma`，不是 `1/(1+sigma)`。
- [ ] 只改变 allocation，不改变 selected codes。
- [ ] preserved base exposure = filled/top_n。
- [ ] 50% cap 确定性执行。
- [ ] 缺失 vol 整体 fallback equal slots。
- [ ] inverse-vol 在 staging/carry 前执行。

## 22.5 R63

- [ ] Entry Top3 / Exit Top4。
- [ ] positive slope 失败立即退出。
- [ ] recluster reset buffer。
- [ ] state 保存 selected clusters 而非单纯 final weights。

## 22.6 R64

- [ ] 不运行 clustering/gates/representative selection。
- [ ] correlation 使用 52W PIT weekly returns。
- [ ] pairwise minimum 20 weeks。
- [ ] corr 必须严格 `<0.80`。
- [ ] negative corr 允许。
- [ ] unknown corr 不当作 0。
- [ ] 不伪造 cluster artifacts。

---

# 23. 验收条件

本设计完成实现时，应满足：

1. R60–R64 五个新策略均可独立出现在 strategy catalog 中；
2. R59 原策略 source 和行为未改变；
3. 每个新策略都有独立、针对其主要机制的单元测试；
4. 公共基金轮动回归测试通过；
5. 每个策略可通过相同公共 Runner 完成回测；
6. paired comparison 可以证明数据/执行 identity 一致；
7. 每个策略的 diagnostics 能明确解释：为什么某 ETF 被选、被过滤、被减权、被 buffer 保留或被 correlation 拒绝；
8. 每个实验结果进入 append-only ledger；
9. 不把历史研究期结果标记为 untouched OOS；
10. 不因 R60–R64 任一结果修改已冻结 Champion gates。

---

# 24. 最终决策框架

本阶段目标不是尽快得到“R65 超级策略”，而是建立以下证据链：

```text
R59
│
├─ R60：中期趋势 gate 是否有效？
├─ R61：多时间尺度 ranking 是否有效？
├─ R62：风险配置是否有效？
├─ R63：换手迟滞是否有效？
└─ R64：复杂聚类是否真的必要？
```

只有每个问题分别得到答案后，才进入下一层组合：

```text
Signal
    ↓
Risk Allocation
    ↓
Execution Hysteresis
    ↓
Diversification Architecture
```

最终优先选择的不是历史收益最高、规则最多的策略，而是：

\[
\boxed{
机制可解释
+ 参数不敏感
+ 成本后有效
+ 多 fold 一致
+ 复杂度尽可能低
}
\]

这也是 R59 之后的核心研究方向：从继续叠加短期趋势规则，转向验证**独立信息源、独立风险层和独立执行层**是否真正带来可泛化增益。
