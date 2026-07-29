import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import { RunDetail } from "../RunDetail";
import type { RunData } from "@/lib/api";

const apiMock = vi.hoisted(() => ({
  getRun: vi.fn(),
  getRunCode: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});
vi.mock("@/components/charts/CandlestickChart", () => ({
  CandlestickChart: ({ data }: { data: Array<{ close: number }> }) => <div>candlestick:{data[0]?.close}</div>,
}));
vi.mock("@/components/charts/EquityChart", () => ({ EquityChart: () => <div>equity</div> }));
vi.mock("@/components/charts/GraphSignalPanel", () => ({ GraphSignalPanel: () => <div>graph signals</div> }));

const metrics = [{ symbol: "000001.SZ", total_return: 0.12, trade_count: 3 }];

const graphRunWithSymbolMetrics: RunData = {
  status: "success",
  run_id: "graph_123",
  run_context: { strategy_type: "stockpred_graph" },
  chart_symbols: ["000001.SZ"],
  metrics: { total_return: 0.12 } as RunData["metrics"],
  symbol_metrics: metrics,
};

const graphRunWithOneSymbolChart: RunData = {
  ...graphRunWithSymbolMetrics,
  price_series: {
    "000001.SZ": [{ time: "2025-01-03", open: 10, high: 11, low: 9, close: 10.5, volume: 100 }],
  },
  indicator_series: {},
  trade_markers: [],
  graph_signal_series: { "000001.SZ": [] },
};

const graphRunWithEmptySymbolChart: RunData = {
  ...graphRunWithSymbolMetrics,
  price_series: {},
  indicator_series: {},
  trade_markers: [],
  graph_signal_series: {},
};

const plainRunWithMetrics: RunData = {
  ...graphRunWithSymbolMetrics,
  run_id: "plain_123",
  run_context: { strategy_type: "generated_strategy" },
};

function renderRunDetail(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderRunSwitcher() {
  return render(
    <MemoryRouter initialEntries={["/runs/run-a"]}>
      <Link to="/runs/run-a">go to A</Link>
      <Link to="/runs/run-b">go to B</Link>
      <Routes>
        <Route path="/runs/:runId" element={<RunDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RunDetail symbol metrics", () => {
  beforeEach(() => {
    apiMock.getRun.mockReset();
    apiMock.getRunCode.mockReset();
    apiMock.getRunCode.mockResolvedValue({});
  });

  it("shows symbol metrics for a graph run and requests only the expanded symbol", async () => {
    apiMock.getRun
      .mockResolvedValueOnce(graphRunWithSymbolMetrics)
      .mockResolvedValueOnce(graphRunWithOneSymbolChart);
    const user = userEvent.setup();

    renderRunDetail("/runs/graph_123");

    expect(await screen.findByText("Symbol performance")).toBeInTheDocument();
    expect(screen.getByText("Total Return")).toBeInTheDocument();
    expect(screen.getAllByText("+12.00%").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: /^000001\.SZ$/i }));
    await waitFor(() => {
      expect(apiMock.getRun).toHaveBeenLastCalledWith("graph_123", { chart_symbol: "000001.SZ" });
    });
  });

  it("does not prefetch a symbol chart before a row is expanded", async () => {
    apiMock.getRun.mockResolvedValue(graphRunWithSymbolMetrics);

    renderRunDetail("/runs/graph_123");

    expect(await screen.findByText("Symbol performance")).toBeInTheDocument();
    expect(apiMock.getRun).toHaveBeenCalledTimes(1);
    expect(apiMock.getRun).toHaveBeenNthCalledWith(1, "graph_123", { chart_payload: "summary" });
  });
  it("shares an in-flight symbol request across the table and load-all action", async () => {
    let resolveChart!: (value: RunData) => void;
    const chartRequest = new Promise<RunData>((resolve) => {
      resolveChart = resolve;
    });
    apiMock.getRun
      .mockResolvedValueOnce(graphRunWithSymbolMetrics)
      .mockReturnValue(chartRequest);
    const user = userEvent.setup();

    renderRunDetail("/runs/graph_123");

    await screen.findByText("Symbol performance");
    await user.click(screen.getByRole("button", { name: /^000001\.SZ$/i }));
    await user.click(screen.getByRole("button", { name: /load all/i }));

    expect(apiMock.getRun).toHaveBeenCalledTimes(2);
    resolveChart(graphRunWithOneSymbolChart);
    await screen.findByTestId("symbol-chart-000001.SZ");
  });

  it("caches a successful empty symbol chart response", async () => {
    apiMock.getRun
      .mockResolvedValueOnce(graphRunWithSymbolMetrics)
      .mockResolvedValueOnce(graphRunWithEmptySymbolChart);
    const user = userEvent.setup();

    renderRunDetail("/runs/graph_123");

    await screen.findByText("Symbol performance");
    await user.click(screen.getByRole("button", { name: /^000001\.SZ$/i }));
    await screen.findByText("No price data");
    await user.click(screen.getByRole("button", { name: /show only/i }));

    expect(apiMock.getRun).toHaveBeenCalledTimes(2);
  });

  it("ignores an old run chart response after switching runs", async () => {
    let resolveOldChart!: (value: RunData) => void;
    let resolveNewChart!: (value: RunData) => void;
    const oldChartRequest = new Promise<RunData>((resolve) => {
      resolveOldChart = resolve;
    });
    const newChartRequest = new Promise<RunData>((resolve) => {
      resolveNewChart = resolve;
    });
    const runASummary = { ...graphRunWithSymbolMetrics, run_id: "run-a" };
    const runBSummary = { ...graphRunWithSymbolMetrics, run_id: "run-b" };
    const runAChart = { ...graphRunWithOneSymbolChart, run_id: "run-a" };
    const runBChart: RunData = {
      ...graphRunWithOneSymbolChart,
      run_id: "run-b",
      price_series: {
        "000001.SZ": [{ time: "2025-01-03", open: 20, high: 21, low: 19, close: 20.5, volume: 200 }],
      },
    };
    apiMock.getRun.mockImplementation((id: string, params: Record<string, string>) => {
      if (params.chart_payload === "summary") {
        return Promise.resolve(id === "run-a" ? runASummary : runBSummary);
      }
      return id === "run-a" ? oldChartRequest : newChartRequest;
    });
    const user = userEvent.setup();

    render(
      <MemoryRouter initialEntries={["/runs/run-a"]}>
        <Link to="/runs/run-b">switch run</Link>
        <Routes>
          <Route path="/runs/:runId" element={<RunDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByText("Symbol performance");
    await user.click(screen.getByRole("button", { name: /^000001\.SZ$/i }));
    expect(apiMock.getRun).toHaveBeenCalledWith("run-a", { chart_symbol: "000001.SZ" });

    await user.click(screen.getByRole("link", { name: "switch run" }));
    await waitFor(() => {
      expect(apiMock.getRun).toHaveBeenCalledWith("run-b", { chart_payload: "summary" });
      expect(screen.getByRole("button", { name: /^000001\.SZ$/i })).toHaveAttribute("aria-expanded", "false");
    });

    resolveOldChart(runAChart);
    await waitFor(() => expect(screen.queryByText("candlestick:10.5")).not.toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /^000001\.SZ$/i }));
    await waitFor(() => {
      expect(apiMock.getRun).toHaveBeenCalledWith("run-b", { chart_symbol: "000001.SZ" });
    });

    resolveNewChart(runBChart);
    expect((await screen.findAllByText("candlestick:20.5")).length).toBeGreaterThan(0);
    expect(screen.queryByText("candlestick:10.5")).not.toBeInTheDocument();
  });

  it("ignores old summary and code resolutions after A to B to A navigation", async () => {
    let resolveOldSummary!: (value: RunData) => void;
    let resolveOldCode!: (value: Record<string, string>) => void;
    let resolveNewSummary!: (value: RunData) => void;
    let resolveNewCode!: (value: Record<string, string>) => void;
    const oldSummaryRequest = new Promise<RunData>((resolve) => { resolveOldSummary = resolve; });
    const oldCodeRequest = new Promise<Record<string, string>>((resolve) => { resolveOldCode = resolve; });
    const newSummaryRequest = new Promise<RunData>((resolve) => { resolveNewSummary = resolve; });
    const newCodeRequest = new Promise<Record<string, string>>((resolve) => { resolveNewCode = resolve; });
    const runBSummary = { ...graphRunWithSymbolMetrics, run_id: "run-b", prompt: "B summary" };
    const oldASummary = { ...graphRunWithSymbolMetrics, run_id: "run-a", prompt: "old A summary" };
    const newASummary = { ...graphRunWithSymbolMetrics, run_id: "run-a", prompt: "new A summary" };
    let aSummaryCalls = 0;
    let aCodeCalls = 0;
    apiMock.getRun.mockImplementation((id: string, params: Record<string, string>) => {
      if (params.chart_payload !== "summary") throw new Error("unexpected chart request");
      if (id === "run-b") return Promise.resolve(runBSummary);
      aSummaryCalls += 1;
      return aSummaryCalls === 1 ? oldSummaryRequest : newSummaryRequest;
    });
    apiMock.getRunCode.mockImplementation((id: string) => {
      if (id === "run-b") return Promise.resolve({ "b.py": "B code" });
      aCodeCalls += 1;
      return aCodeCalls === 1 ? oldCodeRequest : newCodeRequest;
    });
    const user = userEvent.setup();

    renderRunSwitcher();
    await waitFor(() => expect(aSummaryCalls).toBe(1));
    await user.click(screen.getByRole("link", { name: "go to B" }));
    await screen.findByText("B summary");
    await user.click(screen.getByRole("link", { name: "go to A" }));
    await waitFor(() => expect(aSummaryCalls).toBe(2));

    await act(async () => {
      resolveOldSummary(oldASummary);
      resolveOldCode({ "old.py": "old A code" });
      await Promise.all([oldSummaryRequest, oldCodeRequest]);
    });
    expect(screen.queryByText("old A summary")).not.toBeInTheDocument();
    expect(screen.queryByText("old A code")).not.toBeInTheDocument();

    await act(async () => {
      resolveNewSummary(newASummary);
      resolveNewCode({ "new.py": "new A code" });
      await Promise.all([newSummaryRequest, newCodeRequest]);
    });
    expect(await screen.findByText("new A summary")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^code$/i }));
    expect(await screen.findByText("new A code")).toBeInTheDocument();
    expect(screen.queryByText("old A code")).not.toBeInTheDocument();
  });

  it("ignores an old rejected summary after A to B to A navigation", async () => {
    let rejectOldSummary!: (error: Error) => void;
    let resolveNewSummary!: (value: RunData) => void;
    const oldSummaryRequest = new Promise<RunData>((_resolve, reject) => { rejectOldSummary = reject; });
    const newSummaryRequest = new Promise<RunData>((resolve) => { resolveNewSummary = resolve; });
    const runBSummary = { ...graphRunWithSymbolMetrics, run_id: "run-b", prompt: "B summary" };
    const newASummary = { ...graphRunWithSymbolMetrics, run_id: "run-a", prompt: "new A after rejection" };
    let aSummaryCalls = 0;
    apiMock.getRun.mockImplementation((id: string, params: Record<string, string>) => {
      if (params.chart_payload !== "summary") throw new Error("unexpected chart request");
      if (id === "run-b") return Promise.resolve(runBSummary);
      aSummaryCalls += 1;
      return aSummaryCalls === 1 ? oldSummaryRequest : newSummaryRequest;
    });
    const user = userEvent.setup();

    renderRunSwitcher();
    await waitFor(() => expect(aSummaryCalls).toBe(1));
    await user.click(screen.getByRole("link", { name: "go to B" }));
    await screen.findByText("B summary");
    await user.click(screen.getByRole("link", { name: "go to A" }));
    await waitFor(() => expect(aSummaryCalls).toBe(2));

    await act(async () => {
      rejectOldSummary(new Error("old A summary failed"));
      await oldSummaryRequest.catch(() => undefined);
    });
    expect(screen.queryByText(/run not found/i)).not.toBeInTheDocument();

    resolveNewSummary(newASummary);
    expect(await screen.findByText("new A after rejection")).toBeInTheDocument();
  });

  it("cancels an old load-all session when switching A to B to A", async () => {
    let rejectOldChart!: (error: Error) => void;
    let resolveNewChart!: (value: RunData) => void;
    const oldChartRequest = new Promise<RunData>((_resolve, reject) => { rejectOldChart = reject; });
    const newChartRequest = new Promise<RunData>((resolve) => { resolveNewChart = resolve; });
    let aSummaryCalls = 0;
    let aFirstSymbolCalls = 0;
    apiMock.getRun.mockImplementation((id: string, params: Record<string, string>) => {
      if (params.chart_payload === "summary") {
        if (id === "run-b") {
          return Promise.resolve({ ...graphRunWithSymbolMetrics, run_id: "run-b", prompt: "B bulk summary" });
        }
        aSummaryCalls += 1;
        return Promise.resolve({
          ...graphRunWithSymbolMetrics,
          run_id: "run-a",
          prompt: `A bulk summary ${aSummaryCalls}`,
          chart_symbols: ["000001.SZ", "000002.SZ"],
        });
      }
      if (id === "run-a" && params.chart_symbol === "000001.SZ") {
        aFirstSymbolCalls += 1;
        return aFirstSymbolCalls === 1 ? oldChartRequest : newChartRequest;
      }
      if (id === "run-a" && params.chart_symbol === "000002.SZ") {
        return Promise.resolve({ ...graphRunWithEmptySymbolChart, run_id: "run-a" });
      }
      throw new Error(`unexpected chart request for ${id}`);
    });
    const user = userEvent.setup();

    renderRunSwitcher();
    await screen.findByText("A bulk summary 1");
    await user.click(screen.getByRole("button", { name: /load all/i }));
    await waitFor(() => expect(aFirstSymbolCalls).toBe(1));

    await user.click(screen.getByRole("link", { name: "go to B" }));
    await screen.findByText("B bulk summary");
    await user.click(screen.getByRole("link", { name: "go to A" }));
    await screen.findByText("A bulk summary 2");

    const newLoadAll = screen.getByRole("button", { name: /load all/i });
    expect(newLoadAll).toBeEnabled();
    await user.click(newLoadAll);
    await waitFor(() => expect(aFirstSymbolCalls).toBe(2));

    await act(async () => {
      rejectOldChart(new Error("stale bulk chart failed"));
      await oldChartRequest.catch(() => undefined);
    });
    expect(apiMock.getRun).not.toHaveBeenCalledWith("run-a", { chart_symbol: "000002.SZ" });
    expect(screen.getByRole("button", { name: /^cancel$/i })).toBeInTheDocument();

    resolveNewChart({ ...graphRunWithEmptySymbolChart, run_id: "run-a" });
    await waitFor(() => {
      expect(apiMock.getRun).toHaveBeenCalledWith("run-a", { chart_symbol: "000002.SZ" });
    });
  });

  it("does not show symbol metrics for a non-graph run", async () => {
    apiMock.getRun.mockResolvedValue(plainRunWithMetrics);

    renderRunDetail("/runs/plain_123");

    await screen.findByText("plain_123");
    expect(screen.queryByText("Symbol performance")).not.toBeInTheDocument();
  });

  it("routes legacy schema through LegacyStockPredReport", async () => {
    apiMock.getRun.mockResolvedValue({
      ...plainRunWithMetrics,
      run_context: { metric_schema_version: "legacy_portfolio_like_v1" },
    });

    renderRunDetail("/runs/legacy_123");

    const warning = await screen.findByText(/Legacy report \(non-cohort\)/i);
    const legacyWrapper = warning.closest(".space-y-4");
    expect(legacyWrapper).toContainElement(screen.getByText("Total Return"));
  });

  it("rejects unknown metric schema", async () => {
    apiMock.getRun.mockResolvedValue({
      ...plainRunWithMetrics,
      run_context: { metric_schema_version: "future_schema_v9" },
    });

    renderRunDetail("/runs/unknown_123");

    expect(await screen.findByText(/Unsupported StockPred report schema/i)).toBeInTheDocument();
  });
});
