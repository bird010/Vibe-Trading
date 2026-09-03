# Task 4 R88 Routing-Fix Fresh Review

## Verdict

**PASS**

复审确认上一轮发现的 P1 已修复：R88 在批服务层和专用 runner 层均会触发 `role_universe_codes` 路由。未发现新的 P0/P1。按要求未运行长回测，也未修改实现代码。

## Scope reviewed

- Prior review JSON: `docs/superpowers/plans/2026-09-03-r81-expanded-campaign-task-4-r88-review.json`
- Prior review package: `docs/superpowers/plans/2026-09-03-r81-expanded-campaign-task-4-r88-review-package.md`
- R88 implementation and focused tests
- `agent/src/stockpred/fund_rotation/batch_service.py`
- `agent/scripts/run_r81_combination_batch.py`
- Routing regression tests

## P1 fix verification

- `batch_service.py` contains the exact prefix `ai_rotation_r88_r81_role_r60_gate` in `role_strategy_prefixes`.
- `run_r81_combination_batch.py` contains the same exact prefix in `_execution_rule_loader`.
- Both layers select `snapshot.role_universe_codes` when an R88 variant is present; otherwise they preserve the legacy `snapshot.universe_codes` path.
- The runner regression constructs an R88-only request and asserts that the resulting instruments include `513100.SH`, which is present only in the role universe fixture.
- The batch-service routing implementation was inspected directly. Its existing routing regression covers the same role-pool branch with R86/R87-only variants; no PIT-priority or execution-rule semantics were changed by the R88 fix.

## Causal and inheritance verification

- R88 applies the gate to `self._representatives`, i.e. the current R81 dynamic representatives; it does not reselect representatives or import R60 cluster/member state.
- `_causal` filters bars and adjustment rows to `trade_date <= signal_date` before adjustment and return calculation.
- `compute_adjusted_return_126d` uses the last 127 observations, requires valid positive adjusted closes, and fails closed for missing, insufficient, invalid, or non-finite data.
- R88 inherits `EconomicRoleR81RoleRankBufferSession`, preserving the R87 Top3-entry/Top4-exit rank buffer; the upstream R87/R86 chain preserves the 50% positive-exposure transition cap.
- The R88 gate runs during role ranking before the inherited rank-buffer selection, while the transition cap remains downstream in the evaluation chain.
- R88 synchronizes its `medium_trend_gate` diagnostics into the decision log artifact after the upstream decision is created.

## Tests run

```text
E:\anaconda3\envs\AlphaFin\python.exe -m pytest agent/tests/fund_rotation/test_ai_rotation_r88_r81_role_r60_gate.py agent/tests/fund_rotation/test_ai_rotation_r87_r81_role_rank_buffer.py agent/tests/fund_rotation/test_ai_rotation_r86_r81_transition_cap_50.py agent/tests/fund_rotation/test_economic_role_rotation.py agent/tests/fund_rotation/test_strategy_catalog.py agent/tests/fund_rotation/test_r81_runner_output_root.py -q
```

Result: **57 passed, 1 warning, 8.77s**.

The warning is the pre-existing pytest cache write ACL warning. A broader invocation including the full `test_batch_service.py` was attempted; its non-R88 tests that require `tmp_path` were blocked by the existing `C:\Users\LK\AppData\Local\Temp\pytest-of-LK` ACL, while the selected focused suite passed.

## Residual risks

- The two P2 items from the prior review remain: no dedicated future-row perturbation test and no minimal full-session artifact-equality test.
- The batch-service focused regression is not R88-only, although direct source inspection and the runner's R88-only regression establish the R88 branch in both layers.
- No long paired backtest was run. R88 remains research-only and must still pass the formal paired Champion gate before any strategy decision.

## Backtest decision

The prior P1 routing block is cleared. R88 is eligible for the authorized paired backtest, subject to the campaign's fixed interval, execution contract, and Champion gate.
