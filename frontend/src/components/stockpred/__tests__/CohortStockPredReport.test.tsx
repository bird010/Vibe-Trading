import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CohortStockPredReport } from "../CohortStockPredReport";

const apiMock = vi.hoisted(() => ({
  getCohortMetrics: vi.fn(), getCohortReturns: vi.fn(), getCohortQuality: vi.fn(),
  getCohortSymbols: vi.fn(), getCohortChart: vi.fn(), getCohortPeriodBreakdown: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: apiMock };
});
vi.mock("@/components/charts/CandlestickChart", () => ({
  CandlestickChart: ({ data, markers }: { data: Array<{ close: number }>; markers: Array<{ side: string }> }) => (
    <div>candlestick:{data[0]?.close};markers:{markers.map((marker) => marker.side).join(",")}</div>
  ),
}));

const metrics = { mean_return: 0.1, median_return: 0.1, std_return: 0.1, win_rate: 1, p5: 0, p25: 0, p75: 0, p95: 0, mean_excess_return: 0, positive_excess_ratio: 0, mean_fill_rate: 1, mean_idle_cash_ratio: 0, mean_cost_ratio: 0, mean_unliquidated_ratio: 0, valid_cohort_count: 2, total_cohort_count: 2, hac_se: 0.1, bootstrap_ci: null };
const returns = [{ cohort_id: "c1", committed_capital_return: 0.1, status: "LIQUIDATED" }, { cohort_id: "c2", committed_capital_return: null, status: "FAILED_DATA" }];

describe("CohortStockPredReport", () => {
  beforeEach(() => {
    apiMock.getCohortMetrics.mockResolvedValue(metrics);
    apiMock.getCohortReturns.mockResolvedValue(returns);
    apiMock.getCohortQuality.mockResolvedValue({ ranking_eligible: true, valid_eval_ratio: 1, failures: [] });
    apiMock.getCohortSymbols.mockResolvedValue({ symbols: ["000001.SZ"] });
    apiMock.getCohortChart.mockResolvedValue({ code: "000001.SZ", ohlcv: [{ trade_date: "20250101", open: 10, high: 11, low: 9, close: 10.5, vol: 100 }], orders: [{ cohort_id: "c1", trade_date: "20250101", side: "BUY", price: 10, quantity: 100 }, { cohort_id: "c2", trade_date: "20250102", side: "SELL", price: 11, quantity: 100 }] });
    apiMock.getCohortPeriodBreakdown.mockResolvedValue([{ period: "2025", count: 2, mean_return: 0.1, win_rate: 1 }, { period: "2025Q1", count: 2, mean_return: 0.1, win_rate: 1 }]);
  });

  it("loads symbols and renders candlestick with cohort order markers", async () => {
    const user = userEvent.setup();
    render(<CohortStockPredReport runId="run-1" />);
    await user.click(await screen.findByRole("button", { name: "Stocks" }));
    expect(await screen.findByText("candlestick:10.5;markers:BUY")).toBeInTheDocument();
    expect(apiMock.getCohortSymbols).toHaveBeenCalledWith("run-1");
  });

  it("filters order markers by cohort id", async () => {
    const user = userEvent.setup();
    render(<CohortStockPredReport runId="run-1" />);
    await user.click(await screen.findByRole("button", { name: "Stocks" }));
    await screen.findByText("candlestick:10.5;markers:BUY");
    await user.selectOptions(screen.getByLabelText("Cohort"), "c2");
    expect(await screen.findByText("candlestick:10.5;markers:SELL")).toBeInTheDocument();
  });

  it("does not render a marker for a rejected order", async () => {
    apiMock.getCohortChart.mockResolvedValue({ code: "000001.SZ", ohlcv: [{ trade_date: "20250101", open: 10, high: 11, low: 9, close: 10.5, vol: 100 }], orders: [{ cohort_id: "c1", trade_date: "20250101", side: "SELL", price: 10, executed_quantity: 0, status: "REJECTED" }] });
    const user = userEvent.setup();
    render(<CohortStockPredReport runId="run-1" />);
    await user.click(await screen.findByRole("button", { name: "Stocks" }));
    expect(await screen.findByText("candlestick:10.5;markers:")).toBeInTheDocument();
  });

  it("renders year and quarter stability rows", async () => {
    const user = userEvent.setup();
    render(<CohortStockPredReport runId="run-1" />);
    await user.click(await screen.findByRole("button", { name: "Stability" }));
    await waitFor(() => expect(apiMock.getCohortPeriodBreakdown).toHaveBeenCalledWith("run-1"));
    expect(await screen.findByText("2025Q1")).toBeInTheDocument();
  });

  it("clears old state and ignores a stale run response after runId switches", async () => {
    let resolveOld!: (value: typeof metrics) => void;
    const oldMetrics = new Promise<typeof metrics>((resolve) => { resolveOld = resolve; });
    apiMock.getCohortMetrics.mockImplementation((runId: string) => runId === "old" ? oldMetrics : Promise.resolve({ ...metrics, mean_return: 0.2 }));
    const view = render(<CohortStockPredReport runId="old" />);
    view.rerender(<CohortStockPredReport runId="new" />);

    expect(await screen.findByText("20.00%")).toBeInTheDocument();
    resolveOld(metrics);
    await Promise.resolve();
    expect(screen.getByText("Mean Return").parentElement).toHaveTextContent("20.00%");
  });
});
