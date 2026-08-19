# 基金轮动 Cluster Gate 最小软化设计

- **状态**：Proposed
- **日期**：2026-08-16
- **目标分支**：`data-layer-improve`
- **范围**：`correlation_representative` 策略的 cluster quality gate 控制流
- **设计原则**：只解除 `REJECT -> CASH`，不改变聚类、阈值、代表基金选择、动量和目标权重逻辑

---

## 1. 目标

当前 `MAX_CLUSTER_SHARE` 与 `EFFECTIVE_CLUSTER_COUNT` 同时承担两种职责：

1. 描述聚类结果是否过度集中；
2. 决定策略是否停止投资并切为全现金。

这两个职责应当解耦。

本次采用**最小行为改动**：

> 保留现有 Gate 计算、阈值、`PASS/WARN/REJECT` 状态和 diagnostics，只取消 `REJECT` 对投资控制流的否决权。

修改后：

```text
PASS     -> quality VALID
WARN     -> quality DEGRADED
REJECT   -> quality DEGRADED + strong warning
```

三种状态都继续执行：

```text
representative selection
    -> momentum
    -> ranking
    -> target weights
```

不再执行：

```text
REJECT -> target_weights={} -> cash_weight=1.0
```

---

## 2. 为什么这是最小且正确的边界

`MAX_CLUSTER_SHARE` 和 `EFFECTIVE_CLUSTER_COUNT` 衡量的是**聚类结构的集中度/多样性**。

例如，大量权益 ETF 在某一历史阶段高度同步，形成一个占比很高的大 cluster，这可能是真实市场结构，而不是计算错误。

因此：

```text
cluster 结构不好看
!=
cluster 无法计算
!=
策略必须持有现金
```

应保持以下边界：

```text
能够正常完成 clustering
但 cluster concentration 较差
    -> 质量告警
    -> 继续投资逻辑

无法可靠完成 clustering
    -> INVALID
    -> 保留现有 hard failure 行为
```

本次不重新定义聚类质量模型，只修复“诊断指标直接控制资产配置”的耦合。

---

## 3. 当前行为

当前控制流可简化为：

```text
evaluate_cluster_gates()
    |
    +-- PASS
    |     -> representative
    |     -> momentum
    |     -> target weights
    |
    +-- WARN
    |     -> representative
    |     -> momentum
    |     -> target weights
    |     -> quality DEGRADED
    |
    `-- REJECT
          -> _cycle_rejected = True
          -> representatives cleared
          -> SET_TARGETS({}, cash=1.0)
          -> 后续周期继续 cash
```

现有 `cluster_quality_rejection_decision()` 进一步把 `REJECT` 映射为：

```text
DecisionKind.SET_TARGETS
target_weights = {}
cash_weight = 1.0
quality_status = INVALID
reason_code = CLUSTER_QUALITY_REJECTED
```

因此 cluster concentration 不只是 quality signal，而是直接覆盖策略投资动作。

---

## 4. 目标行为

### 4.1 状态映射

| Gate status | Decision quality | 控制流 | 是否继续产生 target weights |
|---|---|---|---|
| `PASS` | `VALID` | 正常继续 | 是 |
| `WARN` | `DEGRADED` | 正常继续 | 是 |
| `REJECT` | `DEGRADED` + strong warning | 正常继续 | 是 |

其中 `REJECT` 在本次修改后仅表示：

> cluster quality diagnostic 达到现有严重告警阈值。

它**不再表示拒绝 portfolio construction**。

为了保持历史 diagnostics、序列化格式和测试兼容性，本次仍保留 `GateStatus.REJECT` 名称，不新增 `SEVERE_WARN` / `ALERT` 枚举。

后续如果需要语义清理，可以单独迁移名称，但不应与本次行为修复绑定。

### 4.2 目标控制流

```text
evaluate_cluster_gates()
    |
    +-- PASS   -> VALID
    +-- WARN   -> DEGRADED
    `-- REJECT -> DEGRADED + CLUSTER_QUALITY_REJECTED
                     |
                     v
             representative selection
                     |
                     v
                  momentum
                     |
                     v
                   ranking
                     |
                     v
               target weights
```

Gate 只影响 quality/diagnostics，不再改变目标仓位。

---

## 5. 最小代码改动

本次生产代码原则上只涉及两个文件。

### 5.1 `gates.py`

文件：

`agent/backtest/fund_rotation/strategies/correlation_representative/gates.py`

#### 保持不变

以下内容全部不改：

- `GateStatus.PASS/WARN/REJECT`
- `ClusterGateEvaluation`
- `MAX_CLUSTER_SHARE` 计算
- `EFFECTIVE_CLUSTER_COUNT` 计算
- warn/reject 阈值
- `evaluate_cluster_gates()` 的判定逻辑
- 现有 reason/diagnostic 内容

#### 修改

`cluster_quality_rejection_decision()` 不再被策略控制流调用。

最小实现可以先保留该 helper 但变为无调用代码；若确认仓库中没有其他 caller，可直接删除。

**不建议**为了此次修改新增：

- `SoftGateStatus`
- 新 DTO
- 新 artifact schema
- 新 gate registry
- 新 threshold 配置

这些都不是解除 `REJECT -> CASH` 所必需。

---

### 5.2 `strategy.py`

文件：

`agent/backtest/fund_rotation/strategies/correlation_representative/strategy.py`

这里是本次修改的核心。

#### 改动 A：`_recluster()` 不再因 REJECT 提前返回

当前逻辑等价于：

```python
if evaluation.status == GateStatus.REJECT:
    self._cycle_rejected = True
    self._representatives = {}
    return evaluation

self._cycle_rejected = False
self._representatives = select_representatives(...)
```

目标改为：

```python
self._representatives = select_representatives(...)
return evaluation
```

即：无论 `PASS/WARN/REJECT`，只要 clustering 本身有效，都继续 representative selection。

#### 改动 B：删除 REJECT 的即时现金短路

删除类似：

```python
if gate_eval.status == GateStatus.REJECT:
    return cluster_quality_rejection_decision(...)
```

让执行自然进入后续 momentum / ranking / target-weight 逻辑。

#### 改动 C：删除 `_cycle_rejected` 的持续现金短路

当前 REJECT 不只影响当前 rebalance，还会通过 `_cycle_rejected` 在下一次 recluster 前持续返回现金。

该状态只服务于 Hard Gate 行为，因此本次应删除：

```text
_cycle_rejected
```

以及对应的：

- 初始化；
- set/reset；
- signal path 判断；
- 为重建 rejection decision 而存在的辅助逻辑（若删除后无 caller）。

这样可以避免留下“当前周不 cash，但后续周仍 cash”的隐式旧行为。

#### 改动 D：统一 quality 映射

最终 Decision 的 quality 映射调整为：

```python
quality_status = (
    QualityStatus.VALID
    if gate_eval.status == GateStatus.PASS
    else QualityStatus.DEGRADED
)
```

即：

```text
PASS   -> VALID
WARN   -> DEGRADED
REJECT -> DEGRADED
```

对于 `REJECT`，沿用现有 reason code 作为 strong warning：

```text
CLUSTER_QUALITY_REJECTED
```

可在 `quality_reasons` 中保留：

```text
CLUSTER_QUALITY_REJECTED
+ 原有 gate reasons
```

这样不需要引入新的 serialized field，也能让 UI、artifact、日志明确识别严重 cluster quality 告警。

---

## 6. Hard Failure 保持不变

本次修改绝不能把真正的数据/计算失败也软化。

例如现有：

```text
CLUSTERING_DATA_INSUFFICIENT
DecisionKind.INVALID
```

继续保持原行为。

原则：

```text
数据不足 / 无法形成合法相关矩阵 / 无法完成 clustering
    -> hard invalid

clustering 已成功，但 cluster size distribution 不理想
    -> soft diagnostic
```

因此本次只软化：

```text
MAX_CLUSTER_SHARE
EFFECTIVE_CLUSTER_COUNT
```

不扩大到其他失败类型。

---

## 7. Strong Warning 的最小实现

为了避免 schema migration，本次不新增 `warning_level=strong` 字段。

直接使用现有状态和 reason 表达严重性：

```text
gate_status = REJECT
quality_status = DEGRADED
quality_reasons contains CLUSTER_QUALITY_REJECTED
```

消费者可以解释为：

```text
WARN   = 普通 cluster quality warning
REJECT = 严重 cluster quality warning，但不阻断投资
```

这样可以同时满足：

- 保留历史 `REJECT` diagnostics 的可比性；
- 不修改 artifact contract；
- 不新增 UI 协议；
- 不扩大本次改动范围。

---

## 8. 测试设计

只增加证明行为边界所必需的测试。

### 8.1 Gate evaluator 不变

构造超过 reject 阈值的 cluster distribution：

```text
expected gate status = REJECT
```

证明本次没有放宽阈值或改变公式。

### 8.2 REJECT 不再 short-circuit

构造：

```text
clustering valid
+ gate REJECT
+ representatives 可选
+ momentum 可计算
```

断言：

```text
继续产生正常 target weights
```

而不是：

```text
target_weights == {}
cash_weight == 1.0
```

注意：测试应使用 synthetic deterministic fixture，不要求任意真实历史区间必须发生交易。

### 8.3 Quality mapping

分别验证：

```text
PASS   -> VALID
WARN   -> DEGRADED
REJECT -> DEGRADED
```

REJECT 同时保留：

```text
CLUSTER_QUALITY_REJECTED
```

作为 strong warning。

### 8.4 不再存在 cycle rejection

构造一次 recluster 返回 `REJECT`，随后一个普通 signal date：

断言后续周期不会因为此前 `_cycle_rejected` 而直接进入全现金。

### 8.5 真正数据不足仍 INVALID

保持/补充回归测试：

```text
CLUSTERING_DATA_INSUFFICIENT
-> DecisionKind.INVALID
```

证明 Hard Failure 边界没有被误伤。

### 8.6 Diagnostics 保持兼容

断言严重集中情况下仍记录：

```text
gate_status = REJECT
max_cluster_share
max_cluster_share threshold
 effective_cluster_count
 effective_cluster_count threshold
reasons
```

不要求修改现有 artifact schema。

---

## 9. 明确不改的内容

本次禁止顺带修改：

1. `max_cluster_share_warn`
2. `max_cluster_share_reject`
3. `min_effective_cluster_count_warn`
4. `min_effective_cluster_count_reject`
5. `k=8`
6. correlation distance
7. linkage 方法
8. representative candidate 规则
9. representative correlation threshold
10. momentum window / ranking
11. target slot weights
12. liquidity rules
13. PIT / universe
14. tracking-index 去重
15. residual-return clustering
16. comparison/ranking quality policy
17. UI schema

特别禁止通过：

```text
0.80 -> 0.95
2.5  -> 1.5
```

这类阈值调整来达到“产生交易”的目的。

---

## 10. 影响范围与风险

### 10.1 代码改动量

预计：

```text
生产代码：2 files
测试：1~2 files
生产代码 diff：约 20~50 LOC 修改/删除
测试：约 40~100 LOC
```

属于小改动。

### 10.2 行为影响

代码风险低，但**策略行为影响是中等**。

原因是过去所有 `REJECT` 周期被强制转换为现金；修改后，这些周期会按照原有 representative + momentum 逻辑产生仓位。

这属于预期变化，不应通过兼容逻辑保留旧现金行为。

### 10.3 历史产物

无需迁移旧 run/artifact。

旧 run 中：

```text
REJECT -> INVALID -> CASH
```

仍代表旧版本真实行为。

新 run 中：

```text
REJECT -> DEGRADED -> normal portfolio construction
```

通过代码版本/run metadata 区分即可。

---

## 11. 验收标准（DoD）

本次修改完成必须同时满足：

1. `MAX_CLUSTER_SHARE` 公式和阈值完全不变。
2. `EFFECTIVE_CLUSTER_COUNT` 公式和阈值完全不变。
3. 超过 reject 阈值时 diagnostics 仍输出 `GateStatus.REJECT`。
4. `GateStatus.REJECT` 不再直接返回全现金 Decision。
5. `GateStatus.REJECT` 不再清空 representatives。
6. 不再通过 `_cycle_rejected` 在后续周期持续强制现金。
7. REJECT 周期继续执行 representative selection。
8. REJECT 周期继续执行 momentum/ranking。
9. REJECT 周期继续使用原有逻辑产生 target weights。
10. `PASS -> VALID`。
11. `WARN -> DEGRADED`。
12. `REJECT -> DEGRADED + CLUSTER_QUALITY_REJECTED`。
13. `CLUSTERING_DATA_INSUFFICIENT` 等真正计算失败仍为 hard invalid。
14. 不修改 clustering / representative / momentum / weighting / universe 逻辑。
15. 不引入新的 artifact schema 或枚举迁移。

---

## 12. 最终设计结论

本次不需要重新设计 cluster gate，只需要修改它的**作用域**：

```text
当前：
cluster quality gate
    = diagnostics
    + decision quality
    + portfolio veto

修改后：
cluster quality gate
    = diagnostics
    + decision quality
```

portfolio action 重新只由原有投资逻辑决定：

```text
representative
-> momentum
-> ranking
-> target weights
```

因此本次最核心的代码原则只有一句：

> **保留 REJECT 作为严重诊断状态，但删除 REJECT 对 portfolio construction 的控制权。**
