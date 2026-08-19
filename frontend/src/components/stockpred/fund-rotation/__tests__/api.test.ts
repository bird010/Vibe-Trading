/** Phase 5 Task 1 — API client tests. */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  fetchStrategies,
  fetchStrategyDetail,
  submitBatch,
  fetchBatches,
  fetchBatchDetail,
  cancelBatch,
  fetchBatchReports,
  batchArtifactUrl,
  fetchCandidatePool,
} from "../api";

function jsonResponse(body: unknown, status = 200, headers?: Record<string, string>): Response {
  return new Response(JSON.stringify(body), { status, headers });
}

describe("Fund Rotation API", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // ── Catalog ──

  it("fetchStrategies calls GET /strategies", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ catalog_version: "v1", strategies: [], mode: "RESEARCH_ONLY" }),
    );
    const result = await fetchStrategies();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/stockpred/fund-rotation/strategies");
    expect(result.mode).toBe("RESEARCH_ONLY");
    expect(result.strategies).toEqual([]);
  });

  it("fetchStrategyDetail calls GET /strategies/{id}", async () => {
    const detail = {
      strategy_id: "test",
      name: "Test",
      description: "Desc",
      config_schema: {},
      config_schema_hash: "abc",
      default_config: {},
      parameter_descriptions: {},
      artifact_roles: [],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(detail));
    const result = await fetchStrategyDetail("test");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/stockpred/fund-rotation/strategies/test");
    expect(result.data.strategy_id).toBe("test");
    expect(result.etag).toBeNull();
  });

  // ── Batch submit ──

  it("submitBatch calls POST /strategy-batches", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ batch_id: "batch-1", status: "QUEUED" }, 202),
    );
    const request = {
      schema_version: "1",
      idempotency_key: "key-1",
      mode: "RESEARCH_ONLY" as const,
      evaluation_start_date: "20240101",
      evaluation_end_date: "20240131",
      execution: { initial_capital: 100000 },
      variants: [{ strategy_id: "test", params: {} }],
    };
    const result = await submitBatch(request);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("/stockpred/fund-rotation/strategy-batches");
    const init = fetchMock.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(init.method).toBe("POST");
    expect(result.batch_id).toBe("batch-1");
  });

  // ── Batch list ──

  it("fetchBatches calls GET /strategy-batches", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    const result = await fetchBatches();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/stockpred/fund-rotation/strategy-batches?limit=50");
    expect(result).toEqual([]);
  });

  // ── Batch detail ──

  it("fetchBatchDetail calls GET /strategy-batches/{id}", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        batch_id: "batch-1",
        state: { stage: "SUCCEEDED", batch_id: "batch-1", mode: "RESEARCH_ONLY" },
        resolved: { variants: [] },
        child_runs: [],
        mode: "RESEARCH_ONLY",
      }),
    );
    const result = await fetchBatchDetail("batch-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/stockpred/fund-rotation/strategy-batches/batch-1");
    expect(result.batch_id).toBe("batch-1");
  });

  // ── Cancel ──

  it("cancelBatch calls POST /strategy-batches/{id}/cancel", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ batch_id: "batch-1", cancelled: true }));
    const result = await cancelBatch("batch-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const url = fetchMock.mock.calls[0]?.[0] as string;
    expect(url).toContain("/stockpred/fund-rotation/strategy-batches/batch-1/cancel");
    const init = fetchMock.mock.calls[0]?.[1] as Record<string, unknown>;
    expect(init.method).toBe("POST");
    expect(result.cancelled).toBe(true);
  });

  // ── Artifacts ──

  it("batchArtifactUrl returns the correct URL", () => {
    const url = batchArtifactUrl("batch-1", "reports.json");
    expect(url).toContain("/stockpred/fund-rotation/strategy-batches/batch-1/artifacts/reports.json");
  });

  it("fetchBatchReports calls GET artifact endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        contract: { fingerprint: "fp", components: {} },
        ranking: [],
        excluded: [],
        quality_warnings: [],
      }),
    );
    const result = await fetchBatchReports("batch-1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/stockpred/fund-rotation/strategy-batches/batch-1/artifacts/reports.json");
    expect(result.contract.fingerprint).toBe("fp");
  });

  it("fetchCandidatePool calls GET candidate-pool endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ run_id: "run-1", reclusters: [] }),
    );

    const result = await fetchCandidatePool("run-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toContain(
      "/stockpred/fund-rotation/backtests/run-1/candidate-pool",
    );
    expect(result.run_id).toBe("run-1");
  });
});
