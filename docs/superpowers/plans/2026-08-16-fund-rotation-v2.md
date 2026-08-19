# Fund Rotation V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the complete Fund Rotation V2 experience end to end: causal backend evidence artifacts and APIs, holdings timeline, rebalance decision explanation, strategy evidence charts, cross-page navigation, and URL deep links while preserving existing tabs.

**Architecture:** Generate immutable, checksum-published evidence during the backtest run. Expose the evidence through typed read-only endpoints; the frontend loads timeline/index lazily, fetches one decision bundle at a time, and renders intervals rather than daily cells. Keep rotation state in a dedicated hook and wrap the existing K-line/trade chart without changing shared chart defaults.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, pandas, pytest; React 19, TypeScript, Vitest, Testing Library, ECharts, Tailwind CSS.

## Global Constraints

- Preserve the existing `overview`, `equity`, `chart`, and `candidate_pool` tabs; add only `rotation_analysis`.
- Frontend must not join raw signals/candidates/positions/orders/trades by date.
- Frontend must not recompute historical Momentum, Relative Strength, or Volatility.
- Actual holding weight must come from actual position snapshots; target weight is never a fallback for actual weight.
- Cash must be rendered as a separate `_CASH` timeline row.
- Missing V2 artifacts must produce an explicit compatibility state for historical runs.
- Do not add asset exposure heatmaps, fund look-through, Sankey, K-line signal markers, or K-line signal table rows.
- Do not change shared `CandlestickChart` defaults used by other pages.
- Run focused tests after each task and the complete backend/frontend suites before completion.

---

### Task 1: Define and publish causal V2 decision evidence

**Files:**
- Create: `agent/src/stockpred/fund_rotation/decision_evidence.py`
- Modify: `agent/backtest/fund_rotation/runner.py`
- Modify: `agent/backtest/fund_rotation/contracts.py`
- Modify: `agent/src/stockpred/fund_rotation/batch_child_runtime.py`
- Modify: `agent/src/stockpred/fund_rotation/artifact_publisher.py`
- Test: `agent/tests/fund_rotation/test_decision_evidence.py`
- Test: `agent/tests/fund_rotation/test_fund_rotation_batch_api.py`

**Interfaces:**
- Produces `build_holdings_timeline(positions_history, decisions, evaluation_dates) -> dict`.
- Produces `build_rebalance_evidence(result, evaluation_dates, strategy_metadata, decision_trace) -> dict`.
- `FundRotationRunResult` gains `decision_trace: tuple[dict, ...]` with optional strategy-specific evidence.
- Publisher registers common roles `holdings_timeline`, `rebalance_index`, and `rebalance_decisions` as JSON files.

- [ ] **Step 1: Write failing evidence-builder tests**

Add tests covering actual-vs-target weight, Cash, exit/re-entry, interval compression, and before-state causality:

The test module defines `fixture_run_result()` as a small `FundRotationRunResult` factory with two dated decisions, two target snapshots, and no strategy-specific trace.

```python
def test_build_holdings_timeline_keeps_cash_and_actual_weight_separate():
    result = build_holdings_timeline(
        positions_history=[
            {"trade_date": "20240102", "equity": 1000, "cash": 500,
             "holdings": [{"ts_code": "510300.SH", "actual_weight": 0.5,
                            "target_weight": 0.8, "market_value": 500}]},
            {"trade_date": "20240103", "equity": 1000, "cash": 400,
             "holdings": [{"ts_code": "510300.SH", "actual_weight": 0.6,
                            "target_weight": 0.8, "market_value": 600}]},
        ],
        decisions=(),
        evaluation_dates=("20240102", "20240103"),
    )

    cash = next(row for row in result["intervals"] if row["ts_code"] == "_CASH")
    etf = next(row for row in result["intervals"] if row["ts_code"] == "510300.SH")
    assert cash["actual_weight"] == 0.45
    assert etf["actual_weight"] == 0.55
    assert etf["target_weight"] == 0.8

def test_build_rebalance_evidence_before_uses_last_state_before_signal():
    result = build_rebalance_evidence(
        result=fixture_run_result(),
        evaluation_dates=("20240102", "20240103", "20240104"),
        strategy_metadata={"ranking_metric": "momentum"},
        decision_trace=(),
    )
    bundle = result["items"]["20240103"]
    assert bundle["before"]["as_of_date"] == "20240102"
    assert bundle["after_target"]["as_of_signal_date"] == "20240103"
```

- [ ] **Step 2: Run the focused tests and verify the expected missing-symbol failure**

Run:

```bash
python -m pytest agent/tests/fund_rotation/test_decision_evidence.py -q
```

Expected: FAIL because the new builders do not exist yet.

- [ ] **Step 3: Implement the minimal causal builders**

Implement `decision_evidence.py` with these rules:

```python
def build_holdings_timeline(
    positions_history: Sequence[Mapping[str, Any]],
    decisions: Sequence[TargetWeightDecision],
    evaluation_dates: Sequence[str],
) -> dict[str, Any]: ...

def build_rebalance_evidence(
    result: FundRotationRunResult,
    evaluation_dates: Sequence[str],
    strategy_metadata: Mapping[str, Any],
    decision_trace: Sequence[Mapping[str, Any]],
) -> dict[str, Any]: ...
```

Use only dates at or before the signal date for `before`; use the signal's target decision for `after_target`; preserve each decision's quality status and diagnostics. Build intervals from actual snapshot membership, compute interval actual weight as the arithmetic time-weighted mean of the supplied actual daily weights, and emit `_CASH` from the snapshot cash/equity ratio. Do not infer ranking evidence from target weights.

- [ ] **Step 4: Extend the run result and publish the three JSON artifacts**

Add `decision_trace` to `FundRotationRunResult`. Add fixed filenames to `COMMON_ROLES`, then have `batch_child_runtime.py` call the builders after execution and publish:

```python
publisher.publish(StrategyArtifact(
    role="holdings_timeline", media_type="application/json", payload=timeline
))
publisher.publish(StrategyArtifact(
    role="rebalance_index", media_type="application/json", payload=index
))
publisher.publish(StrategyArtifact(
    role="rebalance_decisions", media_type="application/json", payload=decisions
))
```

Keep publication optional for partial/legacy results and let manifest checksums protect all three files.

- [ ] **Step 5: Add strategy trace plumbing for the representative strategy**

Extend the strategy diagnostic contract so `correlation_representative` can emit one structured trace record per signal. The record must include `signal_date`, `cluster_snapshot_date`, `strategy`, `cluster_snapshot`, and `candidates`. Do not change target-selection behavior. When a strategy has no trace, publish the bundle with an empty candidate list.

- [ ] **Step 6: Run backend focused tests**

Run:

```bash
python -m pytest agent/tests/fund_rotation/test_decision_evidence.py agent/tests/fund_rotation/test_fund_rotation_batch_api.py -q
```

Expected: all focused evidence and existing batch tests pass.

- [ ] **Step 7: Commit the task**

```bash
git add agent/backtest/fund_rotation agent/src/stockpred/fund_rotation agent/tests/fund_rotation
git commit -m "feat: publish fund rotation v2 decision evidence"
```

### Task 2: Add typed backend APIs for timeline, index, decision bundles, and strategy evidence

**Files:**
- Modify: `agent/src/stockpred/fund_rotation/api_models.py`
- Modify: `agent/src/api/fund_rotation_routes.py`
- Modify: `agent/tests/fund_rotation/test_backtest_detail_api.py`
- Create: `agent/tests/fund_rotation/test_fund_rotation_v2_api.py`

**Interfaces:**
- `GET /stockpred/fund-rotation/backtests/{run_id}/holdings-timeline -> HoldingsTimelineResponse`.
- `GET /stockpred/fund-rotation/backtests/{run_id}/rebalances -> RebalanceIndexResponse`.
- `GET /stockpred/fund-rotation/backtests/{run_id}/rebalances/{signal_date} -> RebalanceDecisionResponse`.
- `InstrumentChartResponse.strategy_evidence` is optional and schema-versioned.

- [ ] **Step 1: Write failing API contract tests**

Build a checksum-published fixture run and assert the three endpoints return the published JSON, reject an invalid signal date, and return a stable 404 code for a legacy run without the artifacts:

```python
def test_rebalance_bundle_is_read_from_checksum_validated_artifact(tmp_path):
    _publish_v2_run_with_rotation_evidence(tmp_path)
    client = TestClient(_app(tmp_path))
    response = client.get(
        "/stockpred/fund-rotation/backtests/run-v2/rebalances/20240103"
    )
    assert response.status_code == 200
    assert response.json()["signal_date"] == "20240103"

def test_legacy_run_gets_explicit_rotation_artifact_error(tmp_path):
    _publish_legacy_detail_only_run(tmp_path)
    response = TestClient(_app(tmp_path)).get(
        "/stockpred/fund-rotation/backtests/legacy/holdings-timeline"
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ROTATION_TIMELINE_UNAVAILABLE"
```

- [ ] **Step 2: Run the tests to verify the missing endpoint/model failure**

Run:

```bash
python -m pytest agent/tests/fund_rotation/test_fund_rotation_v2_api.py -q
```

Expected: FAIL because the routes and response models are not defined.

- [ ] **Step 3: Add Pydantic response models**

Define typed models for holding intervals, markers, snapshots, candidate rows, execution orders/summary, strategy metadata, strategy evidence series, and the three top-level responses. Keep nullable fields optional and normalize dates with the existing canonical date helper.

- [ ] **Step 4: Add checksum-gated route helpers**

Implement `_validated_json_artifact(run_dir, manifest, role, missing_code)` using `_validated_artifact`. Return stable `HTTPException` details for missing artifacts, invalid JSON, and missing signal dates. Validate that `signal_date` is an exact bundle key and that returned `run_id` matches the path.

- [ ] **Step 5: Extend the chart endpoint with optional strategy evidence**

Read the strategy evidence artifact when present, filter points to the requested instrument, and return it beside existing OHLCV/trades/orders. If absent, return the existing chart payload unchanged with `strategy_evidence` omitted.

- [ ] **Step 6: Run backend API and regression tests**

Run:

```bash
python -m pytest agent/tests/fund_rotation/test_fund_rotation_v2_api.py agent/tests/fund_rotation/test_backtest_detail_api.py -q
```

- [ ] **Step 7: Commit the task**

```bash
git add agent/src/api/fund_rotation_routes.py agent/src/stockpred/fund_rotation/api_models.py agent/tests/fund_rotation
git commit -m "feat: expose fund rotation v2 evidence APIs"
```

### Task 3: Add frontend contracts, API client, lazy rotation state, and the new tab

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/types.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/api.ts`
- Create: `frontend/src/components/stockpred/fund-rotation/useRotationAnalysis.ts`
- Create: `frontend/src/components/stockpred/fund-rotation/RotationAnalysisTab.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/rotationApi.test.ts`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/useRotationAnalysis.test.ts`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/RotationAnalysisTab.test.tsx`

**Interfaces:**
- `fetchHoldingsTimeline(runId, signal) -> Promise<HoldingsTimelineResponse>`.
- `fetchRebalanceIndex(runId, signal) -> Promise<RebalanceIndexResponse>`.
- `fetchRebalanceDecision(runId, signalDate, signal) -> Promise<RebalanceDecisionResponse>`.
- `useRotationAnalysis(runId)` owns loading/error/cache state and `selectSignalDate(signalDate)`.

- [ ] **Step 1: Write failing API and hook tests**

Assert exact URL paths, abort propagation, per-signal caching, and that entering the new tab loads only timeline/index:

```ts
it("fetches timeline and rebalance index from the new endpoints", async () => {
  fetchMock.mockResolvedValueOnce(jsonResponse(timelineFixture));
  await fetchHoldingsTimeline("run-1");
  expect(fetchMock.mock.calls[0]?.[0]).toContain(
    "/backtests/run-1/holdings-timeline",
  );
});
```

- [ ] **Step 2: Run the focused Vitest tests and verify the missing exports failure**

Run:

```bash
cd frontend
npm test -- --run src/components/stockpred/fund-rotation/__tests__/rotationApi.test.ts
```

Expected: FAIL because the new types, API functions, and hook do not exist.

- [ ] **Step 3: Add TypeScript contracts and API functions**

Extend `BacktestDetailTab` with `rotation_analysis`, add nullable evidence types, and implement the three fetchers through `withAuthQuery`, using `responseError` and optional AbortSignal exactly like existing detail APIs.

- [ ] **Step 4: Implement the dedicated hook**

Use separate AbortControllers/request ids for timeline/index and decision bundle. Load timeline and index in parallel on `loadOverview()`. Cache `rebalanceDetails[signalDate]`; repeated selection must not refetch. Reset all rotation state when `runId` changes or closes.

- [ ] **Step 5: Add the new tab without changing existing tabs**

Insert the `rotation_analysis` tab between equity and chart. Render `RotationAnalysisTab` only after the base detail has loaded. It must show the timeline/index loading and empty states without triggering chart or candidate-pool requests.

- [ ] **Step 6: Run focused frontend tests**

Run:

```bash
npm test -- --run src/components/stockpred/fund-rotation/__tests__/rotationApi.test.ts src/components/stockpred/fund-rotation/__tests__/useRotationAnalysis.test.ts src/components/stockpred/fund-rotation/__tests__/RotationAnalysisTab.test.tsx
```

- [ ] **Step 7: Commit the task**

```bash
git add frontend/src/components/stockpred/fund-rotation
git commit -m "feat: add fund rotation analysis tab foundation"
```

### Task 4: Implement holdings timeline, brush, semantic zoom, and rebalance navigator

**Files:**
- Create: `frontend/src/components/stockpred/fund-rotation/holdings/HoldingsWeightTimeline.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/holdings/HoldingsTimeBrush.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/holdings/HoldingTooltip.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/rebalance/RebalanceNavigator.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/RotationAnalysisTab.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/__tests__/HoldingsWeightTimeline.test.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/__tests__/RebalanceNavigator.test.tsx`

**Interfaces:**
- `HoldingsWeightTimeline({ data, selectedSignalDate, window, onWindowChange, onSelectSignalDate })`.
- `HoldingsTimeBrush({ startDate, endDate, window, onChange, onReset })`.
- `RebalanceNavigator({ items, selectedSignalDate, filter, onFilterChange, onSelect })`.

- [ ] **Step 1: Write failing rendering and interaction tests**

Cover Cash row, interval rectangles, selected marker, click-to-signal, brush reset, semantic zoom labels, important-change filter, and previous/next navigation.

- [ ] **Step 2: Run tests and verify the missing component failure**

Run:

```bash
npm test -- --run src/components/stockpred/fund-rotation/__tests__/HoldingsWeightTimeline.test.tsx src/components/stockpred/fund-rotation/__tests__/RebalanceNavigator.test.tsx
```

Expected: FAIL because the components are not defined.

- [ ] **Step 3: Implement timeline normalization and semantic zoom helpers**

Create pure helpers inside the timeline module for date-to-pixel mapping, interval clipping, weighted row ordering, long-tail grouping, and zoom mode (`year`, `month`, `week`). Sort rows by weighted holding duration on overview; after a completed brush change, promote instruments held in the current window without changing order on every pointer move.

- [ ] **Step 4: Render SVG intervals and markers**

Render one SVG group per instrument and one rect per interval. Color encodes actual weight intensity; show a percentage label only when the rect is wide enough. Add Cash as a normal row, render selected signal markers, and expose keyboard/click handlers with tooltips.

- [ ] **Step 5: Implement brush and navigator**

Use an SVG brush range with pointer drag and double-click reset. The navigator defaults to changed positions and supports all, full cash, DEGRADED, and REJECTED filters. Previous/next uses the filtered ordered index.

- [ ] **Step 6: Wire selection to lazy decision loading**

Selecting an interval marker calls `selectSignalDate`, scrolls the decision area into view, and preserves the selected date while the bundle loads.

- [ ] **Step 7: Run focused frontend tests and build type-check**

```bash
npm test -- --run src/components/stockpred/fund-rotation/__tests__/HoldingsWeightTimeline.test.tsx src/components/stockpred/fund-rotation/__tests__/RebalanceNavigator.test.tsx
npm run build
```

- [ ] **Step 8: Commit the task**

```bash
git add frontend/src/components/stockpred/fund-rotation
git commit -m "feat: add holdings timeline and rebalance navigation"
```

### Task 5: Implement Before/After, WHY, Ranking Lane, and execution summary

**Files:**
- Create: `frontend/src/components/stockpred/fund-rotation/rebalance/PortfolioChangeChart.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/rebalance/WhyDecisionPanel.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/rebalance/StrategyPipeline.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/rebalance/ClusterRepresentativeMap.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/rebalance/RankingLane.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/rebalance/ExecutionSummary.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/RotationAnalysisTab.tsx`
- Create: `frontend/src/components/stockpred/fund-rotation/__tests__/DecisionPanels.test.tsx`

**Interfaces:**
- `PortfolioChangeChart({ before, afterTarget, onInstrumentClick })`.
- `WhyDecisionPanel({ decision, candidateView, onCandidateViewChange, onInstrumentClick })`.
- `RankingLane({ candidates, topN, primaryMetric })`.
- `ExecutionSummary({ execution })`.

- [ ] **Step 1: Write failing decision-panel tests**

Cover NEW, KEEP, DROP, MISS/CUTOFF, SAME_CLUSTER_EXCLUDED, representative star, rejected cluster quality, full-cash decision, missing ranking evidence, and execution filled/partial/blocked counts.

- [ ] **Step 2: Run focused tests and verify the missing component failure**

```bash
npm test -- --run src/components/stockpred/fund-rotation/__tests__/DecisionPanels.test.tsx
```

Expected: FAIL because the panel components are not defined.

- [ ] **Step 3: Implement the portfolio dumbbell**

Union the before/target instruments, sort by absolute weight change, render old/new dots and a connecting line, and label status from weights (`NEW`, `DROP`, `KEEP`, `REBALANCE`). Never infer money flow between two funds.

- [ ] **Step 4: Implement strategy pipeline and cluster map**

Render strategy metadata from the bundle, not hard-coded strategy names. Show representative plus up to two strongest excluded members by default, `+N more`, expandable clusters, and cluster quality actual/threshold values with warning styling for REJECTED/DEGRADED.

- [ ] **Step 5: Implement Ranking Lane**

Use the bundle's `primary_metric`, rank, selected state, previous/target weights, exclusion stage and reason. Draw a cutoff line at Top-N, show selected/currently dropped semantics, and support `changed`, `top`, `all` views. Candidates excluded at cluster stage must never be rendered as ranking candidates unless the backend explicitly places them in the ranking evidence.

- [ ] **Step 6: Implement execution summary and fallback states**

Render target → order → fill rows and aggregate counts/commission/turnover. If `candidates` is empty, show the exact historical-evidence fallback while keeping portfolio and execution visible.

- [ ] **Step 7: Wire the decision area into RotationAnalysisTab**

Keep WHY wider than the portfolio and execution sections. Show loading, 404 unavailable, quality warnings, and bundle errors independently from timeline errors.

- [ ] **Step 8: Run focused tests and build**

```bash
npm test -- --run src/components/stockpred/fund-rotation/__tests__/DecisionPanels.test.tsx
npm run build
```

- [ ] **Step 9: Commit the task**

```bash
git add frontend/src/components/stockpred/fund-rotation
git commit -m "feat: explain fund rotation rebalance decisions"
```

### Task 6: Publish and render K-line strategy evidence

**Files:**
- Modify: `agent/backtest/fund_rotation/runner.py`
- Modify: `agent/src/stockpred/fund_rotation/batch_child_runtime.py`
- Modify: `agent/src/api/fund_rotation_routes.py`
- Modify: `agent/src/stockpred/fund_rotation/api_models.py`
- Test: `agent/tests/fund_rotation/test_strategy_evidence.py`
- Create: `frontend/src/components/stockpred/fund-rotation/chart/FundRotationStrategyEvidenceChart.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/WeeklyKlineEvidence.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/FundRotationStrategyEvidenceChart.test.tsx`

**Interfaces:**
- Backend publishes `strategy_evidence.json` with instrument-keyed series and actual benchmark.
- `FundRotationStrategyEvidenceChart({ chart, focusDate, selectedIndicator, onIndicatorChange })`.
- Existing `TradeMarkersChart` and shared `CandlestickChart` remain compatible.

- [ ] **Step 1: Write failing backend and frontend evidence tests**

Assert that evidence comes from the runtime trace/artifact, that the benchmark code is not hard-coded, that Momentum is the default, RS/Volatility switch correctly, and missing evidence leaves the existing chart usable.

- [ ] **Step 2: Run focused tests and verify the expected failures**

```bash
python -m pytest agent/tests/fund_rotation/test_strategy_evidence.py -q
cd frontend
npm test -- --run src/components/stockpred/fund-rotation/__tests__/FundRotationStrategyEvidenceChart.test.tsx
```

- [ ] **Step 3: Add runtime evidence contract and publication**

Register `strategy_evidence` as a common publisher role with filename `strategy_evidence.json`. Add a strategy evidence payload with `id`, `label`, `formula_id`, `window`, `unit`, and finite dated points. Use the strategy's actual benchmark series and decision inputs. Do not calculate a substitute Momentum in the UI or chart endpoint.

- [ ] **Step 4: Add the evidence chart wrapper**

Render the existing price/trade chart and a separate ECharts line panel for the selected strategy series. Default to Momentum, expose RS/Volatility only when present, and display a fallback message when no strategy evidence exists.

- [ ] **Step 5: Change K-line loading to the selected instrument path**

Preserve existing trade-table behavior, but allow the rotation analysis deep link to request only the selected instrument. Existing chart-tab tests must continue to pass for legacy multi-instrument mode.

- [ ] **Step 6: Run backend/frontend evidence tests and build**

```bash
python -m pytest agent/tests/fund_rotation/test_strategy_evidence.py agent/tests/fund_rotation/test_backtest_detail_api.py -q
cd frontend
npm test -- --run src/components/stockpred/fund-rotation/__tests__/FundRotationStrategyEvidenceChart.test.tsx src/components/stockpred/fund-rotation/__tests__/WeeklyKlineEvidence.test.tsx
npm run build
```

- [ ] **Step 7: Commit the task**

```bash
git add agent/backtest/fund_rotation agent/src/api agent/src/stockpred/fund_rotation frontend/src/components/stockpred/fund-rotation
git commit -m "feat: add fund rotation strategy evidence charts"
```

### Task 7: Add URL state, cross-tab links, compatibility states, and performance guards

**Files:**
- Modify: `frontend/src/components/stockpred/fund-rotation/useBacktestDetail.ts`
- Modify: `frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/RotationAnalysisTab.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/WeeklyKlineEvidence.tsx`
- Modify: `frontend/src/components/stockpred/fund-rotation/BacktestDetailPanel.tsx` (existing candidate-pool section)
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/fundRotationDeepLinks.test.tsx`
- Test: `frontend/src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx`

**Interfaces:**
- URL parser returns `{ runId, tab, signalDate, instrument, focusDate, strategyIndicator }`.
- Meaningful tab/signal/instrument changes use browser history; viewport brush changes use replace semantics.

- [ ] **Step 1: Write failing deep-link tests**

Cover initial URL restoration, selection updates, browser Back restoration, WHY → K-line, candidate snapshot → rotation analysis, and missing-artifact messages.

- [ ] **Step 2: Run focused tests and verify failure**

```bash
cd frontend
npm test -- --run src/components/stockpred/fund-rotation/__tests__/fundRotationDeepLinks.test.tsx
```

- [ ] **Step 3: Implement URL parsing and synchronization**

Use `URLSearchParams` around the existing detail state. Accept both `YYYYMMDD` and `YYYY-MM-DD` input, normalize internally to the API key format, and ignore invalid dates/instruments. Update tab/signal/instrument changes with `pushState`; update brush viewport with `replaceState`; listen to `popstate` and restore the state.

- [ ] **Step 4: Implement cross-tab callbacks**

WHY instrument clicks switch to chart, set instrument/focus date/strategy indicator; candidate pool snapshot links resolve the first matching rebalance index item; chart focus remains execution-marker-only and does not add signal markers or signal rows.

- [ ] **Step 5: Add compatibility and performance guards**

Show independent empty states for unavailable timeline, index, bundle, ranking evidence, and strategy evidence. Add a test fixture with at least 1,000 intervals and assert the rendered SVG rect count equals interval count rather than date × instrument count. Avoid per-pointer state updates that reorder rows before brush completion.

- [ ] **Step 6: Run focused deep-link tests and build**

```bash
npm test -- --run src/components/stockpred/fund-rotation/__tests__/fundRotationDeepLinks.test.tsx src/components/stockpred/fund-rotation/__tests__/BacktestDetailPanel.test.tsx
npm run build
```

- [ ] **Step 7: Commit the task**

```bash
git add frontend/src/components/stockpred/fund-rotation
git commit -m "feat: connect fund rotation deep links and fallbacks"
```

### Task 8: Run full verification and update acceptance notes

**Files:**
- Modify: `docs/superpowers/acceptance/2026-08-16-fund-rotation-v2.md`
- Modify: `docs/superpowers/specs/2026-08-16-fund-rotation-v2-design.md` only if verification exposes a contract correction

- [ ] **Step 1: Run backend fund-rotation tests**

```bash
python -m pytest agent/tests/fund_rotation -q
```

Expected: exit code 0 and no failed tests.

- [ ] **Step 2: Run frontend test suite**

```bash
cd frontend
npm run test:run
```

Expected: exit code 0 and no failed tests.

- [ ] **Step 3: Run frontend production build**

```bash
npm run build
```

Expected: TypeScript build and Vite build both exit 0.

- [ ] **Step 4: Inspect the final diff and requirements checklist**

Run:

```bash
git diff --check
git status --short
rg -n "rotation_analysis|holdings-timeline|rebalances|strategy_evidence|Momentum|_CASH" agent frontend/src docs/superpowers
```

Confirm every acceptance criterion in the design document has an implementation or an explicit compatibility fallback.

- [ ] **Step 5: Record verification evidence**

Write the actual test counts, build result, known legacy-artifact fallback behavior, and any environment limitations into `docs/superpowers/acceptance/2026-08-16-fund-rotation-v2.md`.

- [ ] **Step 6: Commit the verification record**

```bash
git add docs/superpowers/acceptance/2026-08-16-fund-rotation-v2.md
git commit -m "docs: record fund rotation v2 acceptance evidence"
```
