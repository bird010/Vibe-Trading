import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
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
vi.mock("@/components/charts/CandlestickChart", () => ({ CandlestickChart: () => <div>candlestick</div> }));
vi.mock("@/components/charts/EquityChart", () => ({ EquityChart: () => <div>equity</div> }));
vi.mock("@/components/charts/GraphSignalPanel", () => ({
  GraphSignalPanel: ({ symbol, points }: { symbol: string; points: unknown[] }) => (
    <div>graph-panel:{symbol}:{points.length}</div>
  ),
}));

function graphRun(overrides: Partial<RunData> = {}): RunData {
  return {
    status: "success",
    run_id: "graph_123",
    run_context: { strategy_type: "stockpred_graph" },
    chart_symbols: ["000001.SZ"],
    ...overrides,
  };
}

function renderRunDetail(runId = "graph_123") {
  return render(
    <MemoryRouter initialEntries={[`/runs/${runId}`]}>
      <Routes>
        <Route path="/runs/:runId" element={<RunDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RunDetail StockPred Graph integration", () => {
  beforeEach(() => {
    apiMock.getRun.mockReset();
    apiMock.getRunCode.mockReset();
    apiMock.getRunCode.mockResolvedValue({});
  });

  it("shows Graph diagnostics only for StockPred Graph runs", async () => {
    apiMock.getRun.mockResolvedValue(graphRun({ chart_symbols: [] }));

    renderRunDetail();

    expect(await screen.findByRole("button", { name: /Graph Diagnostics/i })).toBeInTheDocument();
  });

  it("keeps the Graph tab hidden for normal runs", async () => {
    apiMock.getRun.mockResolvedValue({
      status: "success",
      run_id: "run_123",
      run_context: { strategy_type: "generated_strategy" },
      chart_symbols: [],
    });

    renderRunDetail("run_123");
    await screen.findByText("run_123");

    expect(screen.queryByRole("button", { name: /Graph/i })).not.toBeInTheDocument();
  });

  it("loads and renders Graph signals when the selected symbol is requested", async () => {
    const summary = graphRun();
    const detail = graphRun({
      price_series: {
        "000001.SZ": [
          { time: "2025-01-03", open: 10, high: 11, low: 9, close: 10.5, volume: 100 },
        ],
      },
      graph_signal_series: {
        "000001.SZ": [
          {
            time: "2025-01-03",
            code: "000001.SZ",
            score: 0.82,
            rank: 7,
            direction: "long",
            stage: "expansion",
            action: "buy",
          },
        ],
      },
    });
    apiMock.getRun.mockImplementation((_id: string, params: Record<string, string>) =>
      Promise.resolve(params.chart_symbol ? detail : summary),
    );
    const user = userEvent.setup();

    renderRunDetail();

    await user.click(await screen.findByRole("button", { name: /show only/i }));
    await waitFor(() => {
      expect(apiMock.getRun).toHaveBeenCalledWith("graph_123", { chart_symbol: "000001.SZ" });
    });
    await user.click(screen.getByRole("button", { name: /Graph Diagnostics/i }));
    expect(screen.getByText("graph-panel:000001.SZ:1")).toBeInTheDocument();
  });
});
