# Task 9 完成报告：Batch 5 风险层与防御资产

## 实施范围

新增最小风险层和三个正式策略入口：

- `risk_layers.py`：固定目标波动率、无杠杆约束、现金/固定短债/相对动量防御选择、按独立 U1 identity 计数的 breadth。
- R75：仅增加一个预注册目标波动率 `0.15`，使用 `min(1, target/σ)`，缺失或非法波动率 fail-closed 到现金。
- R76：现金防御基线；另以同一 R76 adapter 正式注册固定短债 arm `ai_rotation_r76_fixed_short_bond`，保证三臂走同一 Catalog/Runner 路径。
- R77：冻结防御池（短债/债券/黄金代码）上的因果相对动量；无有效分数回退现金。
- 显式注册 R75–R77，并同步固定 catalog 测试期望。

没有把绝对动量并入首轮，没有引入杠杆、历史最优防御资产回填或公共 Runner/ledger 改动。

## 验证结果

Focused 风险层测试：`9 passed`。
受影响策略/注册回归：`99 passed`。

Batch 5 登记脚本连续执行两次均 exit 0；脚本哈希和风险层哈希均与 manifest 一致：

- manifest：`D58A183562AEF87A11F057001C1C76A0FE98E31C9F777FEB940BEABFB4D6D22C`
- report：`6277338ACBB4C4102202C69BBD8B9824FE8C4B433443DFE0A4CA4F10E4D088A0`

manifest 状态为 `UNAVAILABLE_INPUTS`，`promotion_allowed=false`，三折为 `0/3`。冻结 U1、四臂相同快照 paired backtest、防御池因果历史和压力场景均不可用，因此 Calmar、MDD、现金占用、防御换手、fold contribution、CAGR/Sharpe 及 normal/2x/T+1/T+2 全部保持 `unavailable`，没有作策略晋级结论。

## 最小改动自评

仅新增风险层、三个薄 wrapper、显式 registry 条目、必要 catalog/focused tests、Batch 5 登记脚本和中文报告；R39/R40、公共 Runner、execution ledger、direct-correlation 和历史实验产物均未修改。

## 独立审查门

Task 9 首轮独立审查发现的 3 个 P1 已修复：组合波动率拒绝缺列/NaN、同步 catalog API、将固定短债补为正式策略 ID。第二轮独立 `gpt-5.6-luna/high` reviewer（Galileo）通过：P0/P1/P2/P3=`0/0/1/0`；P2 为固定短债资产不可用时缺少显式策略级测试，Runner 仍会执行前 fail-closed。Task 9 可以进入 Task 10。输入继续缺失时只能维持不可用证据，不得把单元测试或边界登记当作收益证据。
