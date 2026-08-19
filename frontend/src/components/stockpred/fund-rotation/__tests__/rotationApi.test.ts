import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  fetchHoldingsTimeline,
  fetchRebalanceDecision,
  fetchRebalanceIndex,
} from "../api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

describe("Fund Rotation V2 API", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fetches the holdings timeline", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ run_id: "run-1", intervals: [] }));
    const result = await fetchHoldingsTimeline("run-1");
    expect(fetchMock.mock.calls[0]?.[0]).toContain(
      "/stockpred/fund-rotation/backtests/run-1/holdings-timeline",
    );
    expect(result.run_id).toBe("run-1");
  });

  it("fetches the rebalance index", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ run_id: "run-1", items: [] }));
    const result = await fetchRebalanceIndex("run-1");
    expect(fetchMock.mock.calls[0]?.[0]).toContain(
      "/stockpred/fund-rotation/backtests/run-1/rebalances",
    );
    expect(result.items).toEqual([]);
  });

  it("fetches one decision bundle by signal date", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ run_id: "run-1", signal_date: "20240103" }),
    );
    const result = await fetchRebalanceDecision("run-1", "20240103");
    expect(fetchMock.mock.calls[0]?.[0]).toContain(
      "/stockpred/fund-rotation/backtests/run-1/rebalances/20240103",
    );
    expect(result.signal_date).toBe("20240103");
  });
});
