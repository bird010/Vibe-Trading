import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SymbolMetricsTable } from "../SymbolMetricsTable";
import type { ChartPayload } from "../SymbolMetricsTable";
import type { PriceBar, SymbolPerformanceMetrics } from "@/lib/api";
import i18n from "@/i18n";

vi.mock("@/components/charts/CandlestickChart", () => ({
  CandlestickChart: ({ data }: { data: PriceBar[] }) => <div>candlestick:{data[0]?.close}</div>,
}));
vi.mock("@/components/charts/GraphSignalPanel", () => ({
  GraphSignalPanel: () => <div>graph signals</div>,
}));

const bar: PriceBar = {
  time: "2025-01-03",
  open: 10,
  high: 11,
  low: 9,
  close: 10.5,
  volume: 100,
};

const rowA: SymbolPerformanceMetrics = {
  symbol: "000001.SZ",
  total_return: 0.1,
  annual_return: 0.12,
  annual_volatility: 0.2,
  max_drawdown: -0.08,
  sharpe: 1.1,
  sortino: 1.4,
  calmar: 1.5,
  win_rate: 0.55,
  profit_loss_ratio: 1.2,
  trade_count: 8,
  avg_holding_days: 4.5,
};

const rowB: SymbolPerformanceMetrics = {
  ...rowA,
  symbol: "000002.SZ",
  total_return: 0.2,
};

const chartPayload: ChartPayload = {
  price_series: { "000001.SZ": [bar] },
  indicator_series: {},
  trade_markers: [],
  graph_signal_series: {},
};

describe("SymbolMetricsTable", () => {
  afterEach(() => void i18n.changeLanguage("en"));
  it("sorts by total return and loads one chart only once", async () => {
    const user = userEvent.setup();
    const onLoadSymbol = vi.fn().mockResolvedValue(chartPayload);
    render(<SymbolMetricsTable runId="run-a" metrics={[rowA, rowB]} onLoadSymbol={onLoadSymbol} />);

    expect(screen.getAllByRole("row")[1]).toHaveTextContent("000002.SZ");
    expect(screen.getByRole("columnheader", { name: /total return/i })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("button", { name: /total return/i })).not.toHaveAttribute("aria-sort");
    await user.click(screen.getByRole("button", { name: /total return/i }));
    expect(screen.getAllByRole("row")[1]).toHaveTextContent("000001.SZ");
    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    await screen.findByTestId("symbol-chart-000001.SZ");
    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    expect(onLoadSymbol).toHaveBeenCalledTimes(1);
  });

  it("keeps the table usable after one chart request fails", async () => {
    const user = userEvent.setup();
    render(
      <SymbolMetricsTable
        runId="run-a"
        metrics={[rowA, rowB]}
        onLoadSymbol={vi.fn().mockRejectedValue(new Error("chart unavailable"))}
      />,
    );

    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    expect(await screen.findByText("chart unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /000002\.SZ/i })).toBeEnabled();
  });

  it("shows a local empty state when a loaded symbol has no price bars", async () => {
    const user = userEvent.setup();
    render(
      <SymbolMetricsTable
        runId="run-a"
        metrics={[rowA]}
        onLoadSymbol={vi.fn().mockResolvedValue({
          price_series: {},
          indicator_series: {},
          trade_markers: [],
          graph_signal_series: {},
        })}
      />,
    );

    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));

    expect(await screen.findByText("No price data")).toBeInTheDocument();
    expect(screen.queryByText("candlestick")).not.toBeInTheDocument();
  });

  it("uses shared metric labels and a localized symbol header", async () => {
    await i18n.changeLanguage("zh-CN");
    render(<SymbolMetricsTable runId="run-a" metrics={[rowA]} onLoadSymbol={vi.fn()} />);

    expect(screen.getByRole("columnheader", { name: /^总收益率/ })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "标的" })).toBeInTheDocument();
  });
  it("returns no markup when no metrics are available", () => {
    const { container } = render(<SymbolMetricsTable runId="run-a" metrics={[]} onLoadSymbol={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });

  it("uses symbol as a deterministic secondary order for equal and missing metrics", () => {
    const sameMetricLater: SymbolPerformanceMetrics = { ...rowA, symbol: "000004.SZ", total_return: 0.1 };
    const sameMetricEarlier: SymbolPerformanceMetrics = { ...rowA, symbol: "000003.SZ", total_return: 0.1 };
    const missingLater: SymbolPerformanceMetrics = { ...rowA, symbol: "000002.SZ", total_return: Number.NaN };
    const missingEarlier: SymbolPerformanceMetrics = { ...rowA, symbol: "000001.SZ", total_return: Number.NaN };

    render(
      <SymbolMetricsTable
        runId="run-a"
        metrics={[sameMetricLater, missingLater, sameMetricEarlier, missingEarlier]}
        onLoadSymbol={vi.fn()}
      />,
    );

    const dataRows = screen.getAllByRole("row").slice(1);
    expect(dataRows.map((row) => row.textContent)).toEqual([
      expect.stringContaining("000003.SZ"),
      expect.stringContaining("000004.SZ"),
      expect.stringContaining("000001.SZ"),
      expect.stringContaining("000002.SZ"),
    ]);
  });

  it("sets aria-sort only on the active sorting column header", () => {
    render(<SymbolMetricsTable runId="run-a" metrics={[rowA, rowB]} onLoadSymbol={vi.fn()} />);

    const headers = screen.getAllByRole("columnheader");
    expect(headers.filter((header) => header.hasAttribute("aria-sort"))).toHaveLength(1);
    expect(screen.getByRole("columnheader", { name: /total return/i })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: /^symbol$/i })).not.toHaveAttribute("aria-sort");
  });

  it("clears a symbol chart cache when the run changes", async () => {
    const user = userEvent.setup();
    const onLoadSymbol = vi.fn().mockResolvedValue(chartPayload);
    const { rerender } = render(
      <SymbolMetricsTable runId="run-a" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />,
    );

    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    await screen.findByTestId("symbol-chart-000001.SZ");
    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    rerender(<SymbolMetricsTable runId="run-b" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />);
    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));

    expect(onLoadSymbol).toHaveBeenCalledTimes(2);
  });
  it("ignores a rejected chart request from the previous run", async () => {
    let rejectOldRequest!: (error: Error) => void;
    let resolveNewRequest!: (payload: ChartPayload) => void;
    const oldRequest = new Promise<ChartPayload>((_resolve, reject) => {
      rejectOldRequest = reject;
    });
    const newRequest = new Promise<ChartPayload>((resolve) => {
      resolveNewRequest = resolve;
    });
    const onLoadSymbol = vi.fn()
      .mockReturnValueOnce(oldRequest)
      .mockReturnValueOnce(newRequest);
    const user = userEvent.setup();
    const { rerender } = render(
      <SymbolMetricsTable runId="run-a" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />,
    );

    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    rerender(<SymbolMetricsTable runId="run-b" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />);
    await screen.findByRole("button", { name: /000001\.SZ/i, expanded: false });
    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    expect(onLoadSymbol).toHaveBeenCalledTimes(2);
    await act(async () => {
      rejectOldRequest(new Error("old run failed"));
      await oldRequest.catch(() => undefined);
    });

    expect(screen.queryByText("old run failed")).not.toBeInTheDocument();
    expect(screen.getByText(/Loading chart/)).toBeInTheDocument();
    resolveNewRequest(chartPayload);
    expect(await screen.findByTestId("symbol-chart-000001.SZ")).toBeInTheDocument();
    expect(onLoadSymbol).toHaveBeenCalledTimes(2);
  });

  it("ignores an old resolved chart request after A to B to A navigation", async () => {
    let resolveOldRequest!: (payload: ChartPayload) => void;
    let resolveNewRequest!: (payload: ChartPayload) => void;
    const oldRequest = new Promise<ChartPayload>((resolve) => {
      resolveOldRequest = resolve;
    });
    const newRequest = new Promise<ChartPayload>((resolve) => {
      resolveNewRequest = resolve;
    });
    const oldPayload: ChartPayload = {
      ...chartPayload,
      price_series: { "000001.SZ": [{ ...bar, close: 11.5 }] },
    };
    const newPayload: ChartPayload = {
      ...chartPayload,
      price_series: { "000001.SZ": [{ ...bar, close: 12.5 }] },
    };
    const onLoadSymbol = vi.fn()
      .mockReturnValueOnce(oldRequest)
      .mockReturnValueOnce(newRequest);
    const user = userEvent.setup();
    const { rerender } = render(
      <SymbolMetricsTable runId="run-a" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />,
    );

    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    rerender(<SymbolMetricsTable runId="run-b" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />);
    await screen.findByRole("button", { name: /000001\.SZ/i, expanded: false });
    rerender(<SymbolMetricsTable runId="run-a" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />);
    await screen.findByRole("button", { name: /000001\.SZ/i, expanded: false });
    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));

    await act(async () => {
      resolveOldRequest(oldPayload);
      await oldRequest;
    });
    expect(screen.queryByText("candlestick:11.5")).not.toBeInTheDocument();
    expect(screen.getByText(/Loading chart/)).toBeInTheDocument();

    resolveNewRequest(newPayload);
    expect(await screen.findByText("candlestick:12.5")).toBeInTheDocument();
  });

  it("ignores an old rejected chart request after A to B to A navigation", async () => {
    let rejectOldRequest!: (error: Error) => void;
    let resolveNewRequest!: (payload: ChartPayload) => void;
    const oldRequest = new Promise<ChartPayload>((_resolve, reject) => {
      rejectOldRequest = reject;
    });
    const newRequest = new Promise<ChartPayload>((resolve) => {
      resolveNewRequest = resolve;
    });
    const onLoadSymbol = vi.fn()
      .mockReturnValueOnce(oldRequest)
      .mockReturnValueOnce(newRequest);
    const user = userEvent.setup();
    const { rerender } = render(
      <SymbolMetricsTable runId="run-a" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />,
    );

    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));
    rerender(<SymbolMetricsTable runId="run-b" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />);
    await screen.findByRole("button", { name: /000001\.SZ/i, expanded: false });
    rerender(<SymbolMetricsTable runId="run-a" metrics={[rowA]} onLoadSymbol={onLoadSymbol} />);
    await screen.findByRole("button", { name: /000001\.SZ/i, expanded: false });
    await user.click(screen.getByRole("button", { name: /000001\.SZ/i }));

    await act(async () => {
      rejectOldRequest(new Error("stale A failed"));
      await oldRequest.catch(() => undefined);
    });
    expect(screen.queryByText("stale A failed")).not.toBeInTheDocument();
    expect(screen.getByText(/Loading chart/)).toBeInTheDocument();

    resolveNewRequest(chartPayload);
    expect(await screen.findByTestId("symbol-chart-000001.SZ")).toBeInTheDocument();
  });
});
