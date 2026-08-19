import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WeeklyKlineEvidence } from "../WeeklyKlineEvidence";
import type { InstrumentChartResponse } from "../types";
import type { YearlyEvidenceYear } from "../weeklyEvidence";

vi.mock("../TradeMarkersChart", () => ({
  TradeMarkersChart: ({ tsCode, dateRange, focusTime, onMarkerClick }: { tsCode: string; dateRange?: { start: string; end: string }; focusTime?: string; onMarkerClick?: (date: string) => void }) => (
    <>
      <div data-testid={`chart-${tsCode}`}>
        {tsCode} {dateRange?.start}-{dateRange?.end} {focusTime ?? ""}
      </div>
      <button type="button" data-testid={`marker-${tsCode}`} onClick={() => onMarkerClick?.("20240109")}>marker</button>
    </>
  ),
}));

const chart = (tsCode: string): InstrumentChartResponse => ({
  ts_code: tsCode,
  name: "沪深300ETF",
  fund_type: "股票型",
  run_id: "run-1",
  signals: [{ date: "20240108", target_weight: 0.25 }],
  trades: [{ trade_date: "20240109", action: "BUY", status: "FILLED", filled: 10, price: 3.5 }],
  ohlcv: [],
  positions: [],
  orders: [],
  ohlcv_source: { available: true, version: 1 },
  mode: "RESEARCH_ONLY",
});

const year = (yearValue: string, tsCode: string): YearlyEvidenceYear => ({
  year: yearValue,
  instruments: [{ tsCode, chart: chart(tsCode) }],
  events: [
    { kind: "signal", date: `${yearValue}0108`, tsCode, record: { date: `${yearValue}0108`, target_weight: 0.25 } },
    { kind: "trade", date: `${yearValue}0109`, tsCode, record: { trade_date: `${yearValue}0109`, action: "BUY", filled: 10, price: 3.5, status: "FILLED" } },
  ],
});

describe("Yearly K-line evidence", () => {
  it("renders chronological years with a chart on the left and evidence table on the right", () => {
    render(
      <WeeklyKlineEvidence
        years={[year("2025", "159915.SZ"), year("2024", "510300.SH")]}
        charts={{ "510300.SH": chart("510300.SH"), "159915.SZ": chart("159915.SZ") }}
        chartErrors={{}}
        loading={false}
        onRetry={vi.fn()}
      />,
    );

    const headings = screen.getAllByRole("heading", { level: 3 });
    expect(headings.map((heading) => heading.textContent)).toEqual([
      "2024 年 · 操作标的 1 个",
      "2025 年 · 操作标的 1 个",
    ]);
    expect(screen.getByTestId("chart-510300.SH")).toHaveTextContent("510300.SH -");
    expect(screen.getByText(/510300\.SH · 沪深300ETF · 股票型/)).toBeInTheDocument();
    expect(screen.getAllByLabelText("买入")).not.toHaveLength(0);
    expect(screen.queryByText("信号")).toBeNull();
    expect(screen.getAllByRole("table")[0]).toHaveTextContent("目标权重");
    expect(screen.getAllByRole("table")[0]).toHaveTextContent("成交价格");
  });

  it("uses the full backtest range for every instrument chart", () => {
    const first = year("2024", "510300.SH");
    const second = year("2024", "159915.SZ");

    render(
      <WeeklyKlineEvidence
        years={[{ ...first, instruments: [...first.instruments, ...second.instruments] }]}
        charts={{ "510300.SH": chart("510300.SH"), "159915.SZ": chart("159915.SZ") }}
        chartErrors={{}}
        loading={false}
        onRetry={vi.fn()}
        fullDateRange={{ start: "20240101", end: "20241231" }}
      />,
    );

    expect(screen.getByTestId("chart-510300.SH")).toHaveTextContent("20240101-20241231");
    expect(screen.getByTestId("chart-159915.SZ")).toHaveTextContent("20240101-20241231");
  });

  it("keeps a successful instrument visible when another instrument has an error", () => {
    render(
      <WeeklyKlineEvidence
        years={[year("2024", "510300.SH")]}
        charts={{ "510300.SH": chart("510300.SH") }}
        chartErrors={{ "159915.SZ": "K 线不可用" }}
        loading={false}
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByTestId("chart-510300.SH")).toBeInTheDocument();
    expect(screen.getByText(/159915\.SZ.*K 线不可用/)).toBeInTheDocument();
  });

  it("scrolls and highlights the evidence row selected from a chart marker", async () => {
    const first = year("2024", "510300.SH");
    render(
      <WeeklyKlineEvidence
        years={[first]}
        charts={{ "510300.SH": chart("510300.SH") }}
        chartErrors={{}}
        loading={false}
        onRetry={vi.fn()}
      />,
    );

    const marker = screen.getByTestId("marker-510300.SH");
    fireEvent.click(marker);

    await waitFor(() => expect(screen.getByTestId("evidence-row-510300.SH-20240109-0")).toHaveClass("bg-primary/15"));
    expect(screen.getByTestId("chart-510300.SH")).toHaveTextContent("20240109");
  });

  it("moves the matching chart center when a transaction row is clicked", async () => {
    const first = year("2024", "510300.SH");
    render(
      <WeeklyKlineEvidence
        years={[first]}
        charts={{ "510300.SH": chart("510300.SH") }}
        chartErrors={{}}
        loading={false}
        onRetry={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByTestId("evidence-row-510300.SH-20240109-0"));

    await waitFor(() => expect(screen.getByTestId("chart-510300.SH")).toHaveTextContent("20240109"));
  });

  it("shows loading, empty, and retryable all-failed states", () => {
    const { rerender } = render(
      <WeeklyKlineEvidence years={[]} charts={{}} chartErrors={{}} loading onRetry={vi.fn()} />,
    );
    expect(screen.getByText("加载年度 K 线证据…")).toBeInTheDocument();

    rerender(<WeeklyKlineEvidence years={[]} charts={{}} chartErrors={{}} loading={false} onRetry={vi.fn()} />);
    expect(screen.getByText("当前运行没有可展示的年度 K 线证据。")).toBeInTheDocument();

    rerender(
      <WeeklyKlineEvidence
        years={[]}
        charts={{}}
        chartErrors={{ "510300.SH": "请求失败" }}
        loading={false}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText(/请求失败/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
  });
});
