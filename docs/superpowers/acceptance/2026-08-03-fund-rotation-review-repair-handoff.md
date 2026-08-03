# Fund Rotation Review Repair — Local Verification Handoff

Date: 2026-08-03  
Branch: `data-layer-improve`  
Status: implementation submitted; local/CI acceptance pending

## Implemented scope

- Split common data-loading start from each strategy's first decision date.
- Consume the planning-time immutable requirements during execution.
- Snapshot the complete strategy package and an explicit fail-fast framework registry.
- Bind run identity to strategy/framework code, resolved config, pinned data and resolved execution contract.
- Recompute point-in-time ETF eligibility per signal date with current-window adjustment coverage.
- Keep representative ETFs locked between reclustering dates unless a hard tradability/liquidity failure occurs.
- Remove common execution and evaluation fields from the all-members strategy configuration schema.
- Allow mixed strategy frequencies in one batch while retaining a common evaluation calendar.
- Validate batch schema versions, real calendar dates and execution parameter ranges.
- Preserve and publish partial evidence for failed/canceled child runs.
- Add cash, dynamic equal-weight ETF and `510300.SH` benchmark evidence.
- Add relative-performance and execution diagnostics.
- Bind comparison identity to the actual resolved execution contract.
- Repair frontend idempotency, catalog failure visibility, JSON-Schema field rendering and SSE replay/state synchronization.
- Add multi-strategy/common-benchmark equity curves and SVG K-line trade markers.
- Replace namespace-wide Phase 0 golden approvals with exact metric-field approvals.

## Required local checks

### 1. Focused regressions

```bash
cd agent
pytest tests/fund_rotation/test_integrated_review_repairs.py -q
pytest tests/fund_rotation/test_phase0_approved_delta_scope.py -q
pytest tests/fund_rotation/test_strategy_snapshot.py -q
pytest tests/fund_rotation/test_batch_service.py -q
pytest tests/fund_rotation/test_batch_backend_review_regressions.py -q
pytest tests/fund_rotation/test_phase0_golden.py -q
```

### 2. Fund-rotation suite

```bash
cd agent
pytest tests/fund_rotation -q
```

### 3. Full backend suite

```bash
cd agent
pytest -q
```

Tushare or other external integration failures must be classified explicitly. They must not be silently ignored as fund-rotation acceptance.

### 4. Frontend

```bash
cd frontend
npm test -- --run
npm run build
```

Also manually verify:

- a timed-out submission retries with the same idempotency key;
- a running historical batch reconnects SSE from the last sequence;
- terminal status replaces the initial GET state;
- unsupported JSON-Schema fields disable submission;
- comparison charts show all comparable strategies and public benchmarks;
- an ETF detail displays OHLCV, target signals, fills and blocked orders.

### 5. Real pinned-Lance smoke

Run at least two real strategy variants against the production-equivalent paths:

```text
<stockpred_root>/data/lance/market_core
<runs_dir>/fund_rotation
<runs_dir>/fund_rotation/strategy_batches
```

Verify:

- one pinned version per dataset for the complete batch;
- `data_start < decision_start_date < evaluation_start_date` where applicable;
- no decisions are emitted during pure warmup;
- child manifests contain run identity and resolved execution hashes;
- strategy and benchmark equity indices exactly equal the formal evaluation calendar;
- failed/canceled children retain partial evidence but are excluded from comparison;
- only two or more rankable variants produce a formal comparison;
- all manifest checksums validate;
- legacy v1 run files remain byte-for-byte unchanged after reads.

### 6. Main-branch synchronization gate

At the time of implementation the feature branch was substantially diverged from `main`. Do not treat the current branch result as final merge acceptance until the latest `main` is synchronized and all checks above are rerun.

## Acceptance record

Do not rename this handoff to an acceptance record until the local commands, real-data smoke, compatibility fixture and post-main-sync rerun have completed with recorded commit SHAs and outputs.
