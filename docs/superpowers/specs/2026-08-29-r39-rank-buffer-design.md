# R39 排名迟滞实验设计

## 目标

验证 R39 的 Top3/Top4 排名退出缓冲是否能减少 Top3/Top4 边界抖动导致的无效换仓，同时保持 R39 的收益与风险暴露。该实验只进入研究选择区间，不能产生部署资格结论。

## 约束

- Champion 固定为 `ai_rotation_r39_incumbent_carry`。
- 新策略 ID 为 `ai_rotation_r67_r39_rank_buffer`；不得修改 R39、R11、R34 或共享 Runner/执行语义。
- 唯一物质差异是：新聚类 epoch 内，当前排名 <= 3 的簇可进入，上一期已选且当前排名 <= 4 的簇继续保留；重聚类时清空上一期选择。
- staging、incumbent carry、代表 ETF、质量标签、PIT 与交易执行保持不变。
- 使用冻结数据快照、2016-07-01..2022-07-29 研究区间、156/52/52 周滚动折、固定执行合同。

## 验证

先以测试证明 rank buffer 的行为：epoch reset 不保留旧簇；同 epoch 保留 rank 4 的旧簇；rank 5 的旧簇退出并由当前候选填槽；结果确定性且最多 Top3。然后运行聚焦测试和 fund-rotation 回归测试，再由独立 review 检查时间因果、状态隔离、注册表追加和 R39 不变性。

## 回测与决策

以同一 snapshot、fold、质量状态和执行 hash 配对提交 R39 与 R67。仅在所有冻结 Champion gates 通过时提升 R67；否则保留 R39。若 R67 未达到改善门槛，不运行 R68。若 R67 胜出，下一轮才注册 Top3/Top5 作为单独假设；transition cap 线必须另起轮次。
