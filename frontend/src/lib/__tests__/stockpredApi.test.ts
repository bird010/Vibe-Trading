import { api } from "../api";
import { setApiAuthKey } from "../apiAuth";


function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}


describe("StockPred API", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("creates a graph backtest through the dedicated endpoint", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        run_id: "graph_123",
        events_url: "/stockpred/graph/backtests/graph_123/events",
      }),
    );

    const result = await api.createGraphBacktest({
      start: "2025-01-01",
      end: "2025-03-31",
      mode: "parity",
    });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/stockpred/graph/backtests"),
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          start: "2025-01-01",
          end: "2025-03-31",
          mode: "parity",
        }),
      }),
    );
    expect(result.run_id).toBe("graph_123");
  });

  it("loads StockPred status, defaults, and recent runs", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ ready: true, contract: "stockpred-data/v1", tables: [] }))
      .mockResolvedValueOnce(jsonResponse({ mode: "parity", top_n: 50 }))
      .mockResolvedValueOnce(jsonResponse([]));

    await api.getStockPredStatus();
    await api.getGraphBacktestDefaults();
    await api.listGraphBacktests();

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/stockpred/status",
      "/stockpred/graph/defaults",
      "/stockpred/graph/backtests?limit=20",
    ]);
  });

  it("adds the existing API key to the graph event stream URL", () => {
    setApiAuthKey("stockpred secret");

    expect(api.graphBacktestStreamUrl("graph_123")).toBe(
      "/stockpred/graph/backtests/graph_123/events?api_key=stockpred%20secret",
    );
  });
});
