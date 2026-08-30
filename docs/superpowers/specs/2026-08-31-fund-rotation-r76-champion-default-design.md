# R76 固定短债 Champion 与策略批次默认选择设计

## 目标

将 `ai_rotation_r76_fixed_short_bond` 记录为当前基金轮动研究链路的冻结研究 Champion，并将其设置为前端“策略批次配置”的默认选择策略。

## 背景与约束

- 2015–2022 真实回测中，R76 固定短债在可比较策略中表现最佳。
- 证据来源为 batch `bc2c1d09c759`、run `b445a3424213`，有效区间为 `20150105..20221230`。
- 该结果仍为 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`；缺少的可选 identity/PIT 字段不阻塞研究排序，但不得据此开放 promotion 或 deployment。
- 历史 campaign 的 Champion 和 batch 决策文件属于不可变研究证据，不直接覆盖。

## 方案

### 研究 Champion 指针

新增 `experiments/fund_rotation_research_validity/champion.json`，作为当前研究链路的可追踪指针，记录策略 ID、来源 batch/run、snapshot、评价区间、关键指标、质量状态及 promotion/deployment 门禁。

指针状态使用 `FROZEN_RESEARCH_CANDIDATE`，不修改旧实验产物，也不表示生产策略已切换。

### 前端默认策略

修改 `frontend/src/components/stockpred/fund-rotation/FundRotationTab.tsx` 的默认策略常量为 `ai_rotation_r76_fixed_short_bond`。继续保留现有 R11 fallback，策略目录仍由后端动态提供。

仅影响新建策略批次时的初始选择，不改变已有批次、策略实现、后端 Runner、执行语义或 API 合同。

## 验证

- 更新前端默认选择测试，确认首个策略选择器默认值为 R76。
- 运行前端全量测试和构建。
- 校验 Champion 指针与 2015–2022 最终 manifest 的 batch、run、snapshot、区间和指标一致。
- 运行相关 Python 回归测试及 `git diff --check`。
- 使用已有 5.6 Luna reviewer 检查最小改动、P0/P1 和研究-only 边界。

## 非目标

- 不修改 R76 策略算法或既有策略默认参数。
- 不覆盖历史 `champion.json`、旧 campaign decision 或既有回测报告。
- 不将 R76 标记为生产 Champion，不开启 promotion/deployment。
- 不修改 LLM 默认模型、公共 Runner、PIT/data contract 或 Shadow 账本。
