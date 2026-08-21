# Experiment Contract

## Default seed and evidence boundary

When the user does not override them:

- exploratory seed run: `e33b00bd5689`
- seed strategy: `correlation_representative`
- consumed confirmation interval: `2022-08-01..2026-08-01`
- forward-shadow start: `2026-08-20`
- forward-shadow minimum: 104 weeks

The seed reports annual return about 4.43%, maximum drawdown about -12.62%, Sharpe about 0.584, and quality status `RESEARCH_ONLY_UNVERIFIED_UNIVERSE`. Verify these from its artifacts; never copy them into a new experiment without checking.

## Preflight and folds

Find the earliest common PIT-valid date for `fund`, `fact_fund_adj`, and `dim_fund`. All selection data must precede the consumed interval.

Default rolling policy:

```text
Train = 156 weeks
Validation = 52 weeks
Step = 52 weeks
Minimum valid Validation folds = 3
```

Fold parameters are selected from Train only. Validation evaluates them. If the local data cannot support the policy, stop with a data-gap report; do not shorten windows or borrow the consumed interval.

## Frozen execution defaults

Read the values from the seed request and assert they match before freezing:

```json
{
  "initial_capital": 1000000.0,
  "commission_rate": 0.00025,
  "commission_min": 5.0,
  "other_fee_rate": 0.0,
  "max_participation_rate": 0.05,
  "adv_lookback": 20,
  "adv_min_observations": 10,
  "base_slippage_bps": 5.0,
  "max_slippage_bps": 30.0,
  "lot_size": 100
}
```

Freeze the data snapshot, PIT Universe, calendar, benchmarks, random seeds, common Runner, execution policy, and fold definitions. Persist their hashes with every run.

## Champion gate

Compare Champion and Challenger on identical folds and a continuous-account metric contract. Challenger replaces Champion only if all are true:

1. Aggregate Validation Sharpe is strictly higher within the declared numeric tolerance.
2. Validation annual return is not lower.
3. Maximum drawdown worsens by no more than 1 percentage point.
4. Challenger Sharpe wins in more than half of valid folds.
5. No PIT, look-ahead, execution-quality, reconciliation, corruption, or comparability gate fails. `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` is an allowed shared research quality state: if Champion and Challenger have identical snapshot/universe quality status and both are publishable/comparable, it does not by itself fail promotion. The result remains research-only and cannot support deployment qualification.

For a numeric tie, prefer lower drawdown, then lower turnover, then lower complexity; otherwise retain Champion.

## Strategy and review scope

- Strategy IDs: `ai_rotation_rNN_<short-slug>`.
- One principal hypothesis per round.
- New strategy directory and focused tests are allowed.
- Minimal registry changes are allowed. If an exact catalog/registry regression assertion enumerates the registered strategies, the round may update that existing assertion only to append the new strategy ID; all pre-existing IDs and catalog invariants must remain asserted, and no assertion may be deleted, weakened, generalized, or special-cased.
- Existing strategy code/defaults, shared execution semantics, and public data contracts are outside scope.
- Use a fresh reviewer after each fix. Reuse the original implementer for fixes. Five review cycles is the per-round limit.

P0 invalidates the research conclusion or risks destructive effects. P1 materially distorts return/risk, violates an invariant, or breaks core behavior. A round with unresolved P0/P1 does not backtest and does not update Champion.

## API contract

Discover strategy defaults from:

```text
GET /stockpred/fund-rotation/strategies
GET /stockpred/fund-rotation/strategies/{strategy_id}
```

Submit a paired batch through:

```text
POST /stockpred/fund-rotation/strategy-batches
GET  /stockpred/fund-rotation/strategy-batches/{batch_id}
```

The request uses `schema_version="1"`, `mode="RESEARCH_ONLY"`, the frozen execution object, a unique stable idempotency key, and exactly the Champion and Challenger variants. Store the original request.

Wait through SSE or polling until a documented terminal stage. Transient retry reuses the same idempotency key. Validate manifests/checksums, child states, quality status, partial flags, snapshot fingerprint, framework hash, and execution hash before comparing performance.

## Experiment state

At minimum persist:

```text
experiment_spec.json
fold_manifest.json
experiment_ledger.jsonl
champion.json
rounds/round_NN/{analysis.md,design.md,implementation_report.md,review.json,
                 test_results.md,backtest_request.json,backtest_result.json,decision.json}
final_candidate.json
confirmation_report.md
forward_shadow_spec.json
```

Every authorized round reaches a recorded terminal decision. `NO_JUSTIFIED_HYPOTHESIS`, implementation failure, review exhaustion, test failure, technical backtest failure, incomparability, and no improvement all consume a round.
