# Batch 1：PIT U0/U1 身份快照报告

- 快照状态：`unavailable`
- 快照日期数：`0`
- manifest：`E:\code\stock\Vibe-Trading\experiments\fund_rotation_research_validity\batch_1\manifest.json`
- manifest SHA-256：`bcd4f4fdf37adb94b2278d2ea269356d6ad6a8cd60f53b8b0c6b485ceaf20b31`

## 证据边界

U0 复用既有 `UniverseResolver` 的 AS_WAS_KNOWN、上市/退市边界、三层 exclusion 和旧 diagnostics；U1 仅在 U0 上按 underlying index、asset class、region、currency、leveraged/inverse、share-class/feeder 关系确定性去重。未来已知、边界不确定和身份不完整均不进入 U1。

## 研究诊断

available count、duplicate identity ratio、identity hash 和 snapshot fingerprint 在可用输入时逐日期写入 manifest。momentum coverage、max cluster share、effective cluster count 和 tradable representative ratio 需要额外的决策日行情/聚类证据；本次没有输入时明确标记为 `unavailable`，没有用零值替代。

## R39 三折成本/延迟实验

`unavailable`：当前仓库没有可验证的 PIT master、冻结 R39 manifest、三折回测入口及 T+1/T+2 延迟证据，因此本报告不伪造 U0/U1 收益、成本或策略结论。U1 也未被调参。
