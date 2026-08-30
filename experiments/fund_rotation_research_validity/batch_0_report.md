# Batch 0：证据冻结与 summary 指标合同修复

## 结论

已在独立目录生成修复摘要：
`experiments/fund_rotation_research_validity/batch_0/`。

修复仅从源运行的 `strategy_execution_diagnostics.json`（契约
`execution_diagnostics_v2`）投影执行指标；没有重新运行 R39，也没有覆盖源
运行目录。源摘要中的 `turnover=0.0` 被标记为旧摘要口径，正式比较要求显式
携带 v2 契约，缺失或旧契约的摘要不参与排名。

## R39 影响核对

| 指标 | 修复前 | 修复后 |
|---|---:|---:|
| turnover / one_way_turnover | 0.0 | 41.886233690368144 |
| annualized_one_way_turnover | 未提供 | 6.621913983671752 |
| blocked_attempt_rate | 未提供 | 0.13226744186046513 |
| commission | 未提供 | 24232.377515 |
| explicit_fee | 未提供 | 0.0 |
| slippage_opportunity_cost | 未提供 | 54990.48748689036 |

收益、回撤、Sharpe、总收益和 `run_identity_hash` 保持原值；仅补充执行指标
字段。正式数据质量仍为 `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`，因此本产物不把
R39 晋级为 PIT 有效证据。

## 源文件校验和

以下 SHA-256 来自源运行目录，写入独立产物的
`batch_0/repair_manifest.json`；修复脚本只读这些文件：

- `orders.csv`: `66aeff8af735b7efcdfe99325befe9679e472efd10352984ac2f93ace3fafdeb`
- `positions.csv`: `bb07d7a1f2aff2816311276b5bce523020384057cfadf22ac7bb0c43df881669`
- `equity.csv`: `7e121152a785a979dc2a41d084e359f04c2f05a44ba5ba23d2f066aba65fa3f8`
- `trade_events.csv`: `4a0df1a470901ca84711e69cce76b6a21d878df298ae18b47f5ade204dd0f7ed`

源 `summary.json` 校验和为
`0a91e0685d1db3dff6ecc6e7747b006901251f18f08cd0b3728b3f9f6b04f47c`；修复摘要
校验和为
`dfd156a5af5f449b8c575c14e51a83ccf757f2be81c018bcc7924661892cf560`。

## 验证

- Batch 0 投影与修复脚本及类型边界 focused 测试合计：`12 passed`；其中
  `batch_child_runtime` 测试：`7 passed`。
- `git diff --check`：通过。
- 已检查源运行目录未被脚本写入；修复产物单独写入 `batch_0/`。
- 全量 `agent/tests/fund_rotation` 的临时目录曾受到 Windows ACL 限制，属于
  测试环境问题，不能作为业务测试通过证据；将在后续验证中继续单独记录。
