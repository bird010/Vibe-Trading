# R100 Fresh Review 独立审查报告

日期：2026-09-03  
审查对象：`agent/backtest/fund_rotation/strategies/ai_rotation_r86_r81_transition_cap_50/r100_r81_r88_invvol_slots.py` 及其测试、注册和 role-universe routing 修改

## 结论

**P1：当前实现不能通过审查。R86 的 50% transition cap 被 R100 的后置逆波动调整破坏。**

本次只进行了静态审查、纯函数反例验证和定向测试，没有运行长回测，也没有修改实现。

## P1 问题

### 1. R86 cap 被后置权重调整破坏

位置：

- `r100_r81_r88_invvol_slots.py:128-151`
- R86 cap 实现：`agent/backtest/fund_rotation/strategies/ai_rotation_r86_r81_transition_cap_50/strategy.py:49-69`

R100 先调用父类得到已经经过 R86 cap 的 `decision.target_weights`，随后重新计算并写回 selected role 代表权重。这个后置变换没有再次施加 transition cap，因此不能保证：

```text
sum(max(0, target_weight - previous_weight)) <= 0.50
```

纯函数反例：父层已有上一期权重 `A=0.2, B=0.2`，R86 cap 后目标为 `A=0.7, B=0.2`；R100 逆波动调整后为 `A=0.9333, B=0.1333`，相对上一期的正向增量为 `0.7333`，超过 `0.50`。

因此这是明确的策略契约破坏，不是单纯测试缺口。

### 2. 非等权 base 下 target weights 与 cash 不守恒

位置：`r100_r81_r88_invvol_slots.py:108-112`

实现使用所有 factor 的普通平均值作为归一化分母，但调用方在 `r100_r81_r88_invvol_slots.py:137-146` 传入的是 R86/R88 后的非等权 `base_weights`。

反例：`base_weights={A:0.7, B:0.2}`、`cash=0.1`，逆波动 factor 为 `A=1.0, B=0.5` 时，输出为：

```text
A=0.9333, B=0.1333, cash=0.1
总和=1.1667
```

这会产生超过 100% 的目标组合，并进一步放大 cap 破坏。

### 3. 防御资产可能被误纳入槽位调整

位置：`r100_r81_r88_invvol_slots.py:132-151`

`selected_codes` 包含所有选中角色代表。`511010.SH` 同时是默认 BOND 角色代表候选，也是 R88/R81 的固定防御资产；当它既是选中角色代表又承接防御现金时，R100 会调整该代码的总权重，无法区分角色槽位部分和防御部分。

这违反“仅调整已选角色代表、不误改防御资产”的边界。

## 已验证通过的部分

- helper 对缺失代表、quality gate、窗口不足、NaN 和缺失列提供回退逻辑：`r100_r81_r88_invvol_slots.py:76-120`。
- 现有 `CausalDataView.returns()` 按 signal date 截断；R100 使用的 8 周数据在现有 `CLOSE` 信息截止约束下具备因果边界。
- registry 已注册 R100：`agent/backtest/fund_rotation/strategies/registry.py:258-260,351`。
- BatchService 与 runner 的 role-universe routing 已加入 R100：
  - `agent/src/stockpred/fund_rotation/batch_service.py:372-390`
  - `agent/scripts/run_r81_combination_batch.py:29-53`
- 定向测试结果：`30 passed`。

## P2：测试覆盖缺口

位置：`agent/tests/fund_rotation/test_ai_rotation_r100_r81_r88_invvol_slots.py:26-77`

尚未覆盖：

- 非等权 `base_weights` 的守恒；
- R86 cap 与 R100 后置调整的组合约束；
- 防御代表与防御资产代码重合；
- 非槽位资产保持不变；
- session 级 decision log、decision trace、artifact 与 diagnostics 一致性；
- R100 专属 signal-date 因果不变量。

## 审查决定

**返回 P1，不批准进入 R100 长回测。**

本轮不修改实现。后续修复方向应先明确组合顺序和不变量：逆波动调整必须在 cap 约束内完成，且必须使用加权归一化保持 `sum(target_weights)+cash=1`，同时将防御配置与角色槽位权重分离保护。
