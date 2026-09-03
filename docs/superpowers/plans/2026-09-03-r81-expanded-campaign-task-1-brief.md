# Task 1: 修复 R81 固定防御资产资格处理并重建 anchor

**Files:**
- Modify: `agent/backtest/fund_rotation/strategies/economic_role_rotation/strategy.py:400-405`
- Test: `agent/tests/fund_rotation/test_economic_role_rotation.py`
- Create: `agent/tests/fund_rotation/test_r81_fixed_defense_eligibility.py`
- Modify: `agent/scripts/run_r81_combination_batch.py` only if the repaired anchor requires an explicit preflight record; preserve the requested dates.

**Interfaces:**
- Consumes: existing `signal_eligible` set and `apply_defense_asset()` behavior.
- Produces: a legal R81 decision when `511010.SH` is unavailable, with explicit diagnostics/reason code and cash fallback.

**Constraints:**
- The user explicitly authorizes this existing-R81 bugfix; do not modify other existing strategy defaults, the public Runner, PIT/data contract, or execution semantics.
- Evaluation interval must remain `20130329..20220729`.
- When `511010.SH` is in `signal_eligible`, preserve fixed-short-bond behavior.
- When unavailable, do not force it into target weights; preserve cash and record the unavailable reason.
- Follow TDD: write the failing test first, run it and observe the expected failure, then implement the minimum fix.
- Run focused R81 tests and existing economic-role regressions.
- Run the R81 anchor and verify both child variants reach terminal success, no contract violation occurs, and the anchor is publishable/comparable.
- Record repaired implementation hash, run IDs, snapshot, fold manifest and the precondition deviation in the append-only ledger.

**Expected behavior tests:**

```python
def test_fixed_defense_unavailable_falls_back_to_cash():
    # The R81 decision path must not emit 511010.SH when it is absent from
    # the signal-date eligible set.
    assert "511010.SH" not in target_weights
    assert cash_weight > 0.0
    assert reason_code == "FIXED_SHORT_BOND_UNAVAILABLE"

def test_fixed_defense_eligible_is_preserved():
    # When the code is eligible, the fixed short bond receives the available
    # defense cash exactly as before.
    assert target_weights["511010.SH"] == expected_weight
    assert cash_weight == 0.0
```

**Report:** Write the implementation report to:
`E:\code\stock\Vibe-Trading\docs\superpowers\plans\2026-09-03-r81-expanded-campaign-task-1-report.md`.

