# R100 / R29 角色层逆波动槽位组合任务简报

## 目标

在当前 research Champion R88 的完整上游流程之后，只改变已选角色代表的最终槽位权重，验证 R29 的角色层逆波动权重是否改善验证集表现。

## 冻结边界

- 基线：当前 Champion R88（包含 R86 的 50% 正向增量上限、R87 Top3 入选/Top4 退出迟滞、R88 因果 126 个交易日正趋势 gate、R81 动态经济角色代表选择、生命周期、防御和执行合同）。
- 不改变：角色评分、代表选择、代表锁定、gate、刷新周期、换手上限、现金/防御逻辑、信号日期和执行时点。
- 候选 ID：`ai_rotation_r100_r81_r88_invvol_slots`。

## 唯一可证伪假设

在同一组 R88 已选代表和空槽位下，对每个已填槽位读取信号日前最近 8 个完整周度收益，计算总体波动 `sigma`，因子 `f=1/(1+sigma)`；每个槽位按 `1/top_n * f/mean(f)` 调整，空槽位现金保持不变。若质量 gate 非 PASS、窗口不足、代表缺列或出现非有限值，则整体回退 R88 的等权槽位权重。该变化可能降低低波动代表的回撤，并改善风险调整收益。

## 验收标准

1. 先写 RED 测试：权重和/现金非负；只影响已填槽位；空槽位现金不被再投资；完整窗口使用；缺失数据和非 PASS gate 回退；不读未来数据；策略 ID/diagnostics 独立。
2. GREEN 后运行 focused tests 与 R88/经济角色回归；不运行长回测。
3. 由 fresh reviewer 审查；若有 P0/P1，修复后重新审查。
4. 只有审查通过才运行 paired backtest：R88 challenger control vs R100，固定区间 `20130329..20220729`、同一 snapshot/execution contract。
5. Champion gate：验证 Sharpe 严格更高、验证年化收益不低、MDD 恶化不超过 1 个百分点、5 折 Sharpe 至少 3 折获胜，且 PIT/因果/执行/可比性通过。

## 证据来源

- R29 原始实现：`agent/backtest/fund_rotation/strategies/ai_rotation_r29_invvol_slots/strategy.py`。
- 当前 Champion：`agent/backtest/fund_rotation/strategies/ai_rotation_r86_r81_transition_cap_50/r88_r81_role_r60_gate.py`。
- 策略筛选依据：`docs/superpowers/plans/2026-09-03-r81-r21-r30-role-screening.md`。

## 限制

这是研究回测，不部署；若运行器/数据质量报告为 DEGRADED，结论须标记为研究性、未验证 universe。
