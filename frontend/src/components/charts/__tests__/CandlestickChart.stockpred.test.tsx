import { render } from "@testing-library/react";
import { CandlestickChart } from "../CandlestickChart";
import type { PriceBar, TradeMarker } from "@/lib/api";
import i18n from "@/i18n";
import { getChartTheme } from "@/lib/chart-theme";


const chartMock = vi.hoisted(() => ({
  setOption: vi.fn(),
  getOption: vi.fn(() => ({})),
  resize: vi.fn(),
  dispose: vi.fn(),
  dispatchAction: vi.fn(),
  on: vi.fn(),
  off: vi.fn(),
  group: "",
}));

vi.mock("@/lib/echarts", () => ({
  echarts: { init: vi.fn(() => chartMock) },
  CHART_GROUP: "quant-charts",
  connectCharts: vi.fn(),
}));
vi.mock("@/hooks/useDarkMode", () => ({ useDarkMode: () => ({ dark: false }) }));

const BARS: PriceBar[] = [
  { time: "2025-01-03", open: 10, high: 11, low: 9, close: 10.5, volume: 100 },
  { time: "2025-01-06", open: 10.5, high: 12, low: 10, close: 11, volume: 120 },
];

const EXECUTION_MARKERS: TradeMarker[] = [
  { time: "2025-01-03", side: "BUY", price: 10, status: "FILLED", reason: "signal" },
  { time: "2025-01-03", side: "BUY", price: 10.2, status: "PARTIAL", reason: "capacity" },
  { time: "2025-01-06", side: "SELL", price: 11, status: "REJECTED", reason: "limit_down" },
  { time: "2025-01-06", side: "SELL", price: 11.2, status: "FILLED", exit_delay_days: 2, reason: "delayed_exit" },
];

describe("CandlestickChart StockPred execution markers", () => {
  beforeEach(() => {
    chartMock.setOption.mockReset();
    chartMock.getOption.mockReset().mockReturnValue({});
    chartMock.on.mockReset();
    chartMock.off.mockReset();
    chartMock.dispatchAction.mockReset();
  });

  it("moves to a clicked date without changing the current zoom span", () => {
    chartMock.getOption.mockReturnValue({ dataZoom: [{ start: 20, end: 60 }] });

    render(
      <CandlestickChart
        data={[
          ...BARS,
          { time: "2025-01-07", open: 11, high: 13, low: 10, close: 12, volume: 140 },
          { time: "2025-01-08", open: 12, high: 14, low: 11, close: 13, volume: 160 },
          { time: "2025-01-09", open: 13, high: 15, low: 12, close: 14, volume: 180 },
        ]}
        markers={[]}
        focusTime="2025-01-09"
      />,
    );

    expect(chartMock.dispatchAction).toHaveBeenCalledWith(expect.objectContaining({ type: "dataZoom", dataZoomIndex: [0, 1], start: 60, end: 100 }));
  });

  it("does not move when the selected date is already visible", () => {
    chartMock.getOption.mockReturnValue({ dataZoom: [{ start: 0, end: 100 }] });

    render(<CandlestickChart data={BARS} markers={[]} focusTime="2025-01-06" />);

    expect(chartMock.dispatchAction).not.toHaveBeenCalled();
  });

  it("does not reapply a selected date after unrelated chart data updates", () => {
    chartMock.getOption.mockReturnValue({ dataZoom: [{ start: 20, end: 60 }] });
    const data = [
      ...BARS,
      { time: "2025-01-07", open: 11, high: 13, low: 10, close: 12, volume: 140 },
      { time: "2025-01-08", open: 12, high: 14, low: 11, close: 13, volume: 160 },
      { time: "2025-01-09", open: 13, high: 15, low: 12, close: 14, volume: 180 },
    ];
    const { rerender } = render(
      <CandlestickChart data={data} markers={[]} focusTime="2025-01-09" />,
    );
    expect(chartMock.dispatchAction).toHaveBeenCalledTimes(1);

    rerender(<CandlestickChart data={[...data]} markers={[]} focusTime="2025-01-09" />);

    expect(chartMock.dispatchAction).toHaveBeenCalledTimes(1);
  });

  it("treats a repeated row click as a new focus request", () => {
    const data = [
      ...BARS,
      { time: "2025-01-07", open: 11, high: 13, low: 10, close: 12, volume: 140 },
      { time: "2025-01-08", open: 12, high: 14, low: 11, close: 13, volume: 160 },
      { time: "2025-01-09", open: 13, high: 15, low: 12, close: 14, volume: 180 },
    ];
    chartMock.getOption.mockReturnValue({ dataZoom: [{ start: 20, end: 60 }] });
    const { rerender } = render(
      <CandlestickChart data={data} markers={[]} focusTime="2025-01-09" focusRequest={1} />,
    );
    expect(chartMock.dispatchAction).toHaveBeenCalledTimes(1);

    chartMock.getOption.mockReturnValue({ dataZoom: [{ start: 0, end: 20 }] });
    rerender(
      <CandlestickChart data={data} markers={[]} focusTime="2025-01-09" focusRequest={2} />,
    );

    expect(chartMock.dispatchAction).toHaveBeenCalledTimes(2);
  });

  it("forwards clicked execution marker dates", () => {
    const onMarkerClick = vi.fn();
    render(<CandlestickChart data={BARS} markers={EXECUTION_MARKERS} onMarkerClick={onMarkerClick} />);

    const handler = chartMock.on.mock.calls[0]?.[1] as ((params: unknown) => void) | undefined;
    handler?.({ componentType: "markPoint", data: { coord: ["2025-01-03", 10] } });

    expect(onMarkerClick).toHaveBeenCalledWith("2025-01-03");
  });

  it("reports absolute dates when the user changes the zoom range", () => {
    const onZoomRangeChange = vi.fn();
    chartMock.getOption.mockReturnValue({ dataZoom: [{ start: 25, end: 75 }] });

    render(<CandlestickChart data={BARS} markers={[]} onZoomRangeChange={onZoomRangeChange} />);

    const handler = chartMock.on.mock.calls.find(([eventName]) => eventName === "datazoom")?.[1] as ((params: unknown) => void) | undefined;
    handler?.({});

    expect(onZoomRangeChange).toHaveBeenCalledWith({ start: "2025-01-03", end: "2025-01-06" });
  });

  it("preserves a manual zoom range when chart options are updated", () => {
    const { rerender } = render(<CandlestickChart data={BARS} markers={[]} />);

    chartMock.getOption.mockReturnValue({ dataZoom: [{ start: 24, end: 68 }] });
    rerender(<CandlestickChart data={BARS} markers={EXECUTION_MARKERS} />);

    const option = chartMock.setOption.mock.calls[chartMock.setOption.mock.calls.length - 1]?.[0];
    expect(option.dataZoom[0]).toMatchObject({ start: 24, end: 68 });
  });

  it("preserves a shared absolute date range when chart options are updated", () => {
    const data = [
      ...BARS,
      { time: "2025-01-07", open: 11, high: 13, low: 10, close: 12, volume: 140 },
      { time: "2025-01-08", open: 12, high: 14, low: 11, close: 13, volume: 160 },
      { time: "2025-01-09", open: 13, high: 15, low: 12, close: 14, volume: 180 },
    ];
    render(
      <CandlestickChart
        data={data}
        markers={[]}
        sharedZoomRange={{ start: "2025-01-06", end: "2025-01-08" }}
      />,
    );

    const option = chartMock.setOption.mock.calls[chartMock.setOption.mock.calls.length - 1]?.[0];
    expect(option.dataZoom[0]).toMatchObject({ start: 25, end: 75 });
  });

  it("does not report a programmatic shared zoom as a new user zoom", () => {
    const onZoomRangeChange = vi.fn();
    chartMock.getOption.mockReturnValue({ dataZoom: [{ start: 25, end: 75 }] });

    render(
      <CandlestickChart
        data={BARS}
        markers={[]}
        onZoomRangeChange={onZoomRangeChange}
        sharedZoomRange={{ start: "2025-01-03", end: "2025-01-06" }}
      />,
    );

    const sharedZoomPayload = chartMock.dispatchAction.mock.calls
      .map(([payload]) => payload)
      .find((payload) => payload?.type === "dataZoom");
    const handler = chartMock.on.mock.calls.find(([eventName]) => eventName === "datazoom")?.[1] as ((params: unknown) => void) | undefined;
    handler?.({ ...sharedZoomPayload, type: "datazoom" });

    expect(onZoomRangeChange).not.toHaveBeenCalled();
  });

  it("renders safely when ECharts has no current option yet", () => {
    chartMock.getOption.mockReturnValue(undefined);

    expect(() => render(<CandlestickChart data={BARS} markers={[]} />)).not.toThrow();
  });

  it("overlays backend strategy indicators on the right price axis", () => {
    render(
      <CandlestickChart
        data={BARS}
        markers={[]}
        strategyIndicators={{
          Momentum: [
            { time: "2025-01-03", value: 0.8 },
            { time: "2025-01-06", value: 0.82 },
          ],
        }}
      />,
    );

    const option = chartMock.setOption.mock.calls[chartMock.setOption.mock.calls.length - 1]?.[0];
    expect(option.yAxis[2]).toMatchObject({ gridIndex: 0, position: "right" });
    expect(option.series.find((series: { name: string }) => series.name === "Momentum")).toMatchObject({
      type: "line",
      yAxisIndex: 2,
      data: [0.8, 0.82],
    });
  });

  it("connects sparse weekly Strategy Score points without creating daily values", () => {
    render(
      <CandlestickChart
        data={[
          ...BARS,
          { time: "2025-01-07", open: 11, high: 13, low: 10, close: 12, volume: 140 },
        ]}
        markers={[]}
        strategyScore={{
          "策略得分（周频）": [
            { time: "2025-01-03", value: 0.8 },
            { time: "2025-01-07", value: 1.1 },
          ],
        }}
      />,
    );

    const option = chartMock.setOption.mock.calls[chartMock.setOption.mock.calls.length - 1]?.[0];
    const series = option.series.find((item: { name: string }) => item.name === "策略得分（周频）");
    expect(series).toMatchObject({ connectNulls: true, symbol: "circle", showSymbol: true, data: [0.8, null, 1.1] });
  });

  it("renders rejected, partial, and delayed executions with distinct markers", () => {
    render(<CandlestickChart data={BARS} markers={EXECUTION_MARKERS} />);

    const option = chartMock.setOption.mock.calls[chartMock.setOption.mock.calls.length - 1]?.[0];
    const encoded = JSON.stringify(option);
    expect(encoded).toContain('"value":"B"');
    expect(encoded).toContain('"value":"P"');
    expect(encoded).toContain('"value":"X"');
    expect(encoded).toContain('"value":"D"');
    expect(encoded).toContain("REJECTED");
    expect(encoded).toContain("limit_down");
  });

  it("uses a muted color only for normal muted trade markers", () => {
    render(
      <CandlestickChart
        data={BARS}
        markers={[
          { time: "2025-01-03", side: "BUY", price: 10, muted: true },
          { time: "2025-01-06", side: "SELL", price: 11 },
          { time: "2025-01-06", side: "SELL", price: 11.1, status: "REJECTED" },
          { time: "2025-01-03", side: "BUY", price: 10.2, status: "PARTIAL" },
          { time: "2025-01-06", side: "SELL", price: 11.2, exit_delay_days: 2 },
        ]}
      />,
    );

    const theme = getChartTheme();
    const option = chartMock.setOption.mock.calls[chartMock.setOption.mock.calls.length - 1]?.[0];
    const marks = option.series[0].markPoint.data;

    expect(marks.find((mark: { value: string }) => mark.value === "B").itemStyle.color).not.toBe(theme.upColor);
    expect(marks.find((mark: { value: string }) => mark.value === "S").itemStyle.color).toBe(theme.downColor);
    expect(marks.find((mark: { value: string }) => mark.value === "X").itemStyle.color).toBe(theme.textColor);
    expect(marks.find((mark: { value: string }) => mark.value === "P").itemStyle.color).toBe(theme.warningColor);
    expect(marks.find((mark: { value: string }) => mark.value === "D").itemStyle.color).toBe("#8b5cf6");
  });

  it("keeps legacy markers on the B/S mapping", () => {
    render(
      <CandlestickChart
        data={BARS}
        markers={[
          { time: "2025-01-03", side: "BUY", price: 10 },
          { time: "2025-01-06", side: "SELL", price: 11 },
        ]}
      />,
    );

    const encoded = JSON.stringify(
      chartMock.setOption.mock.calls[chartMock.setOption.mock.calls.length - 1]?.[0],
    );
    expect(encoded).toContain('"value":"B"');
    expect(encoded).toContain('"value":"S"');
  });

  it("uses red for rising candles in the Chinese fund-rotation view", async () => {
    await i18n.changeLanguage("zh-CN");
    render(
      <CandlestickChart
        data={[
          { time: "2025-01-03", open: 10, high: 12, low: 9, close: 11, volume: 100 },
          { time: "2025-01-06", open: 11, high: 12, low: 9, close: 10, volume: 120 },
        ]}
        markers={[]}
      />,
    );

    const option = chartMock.setOption.mock.calls[chartMock.setOption.mock.calls.length - 1]?.[0];
    expect(option.series[0].itemStyle).toMatchObject({ color: "#ef4444", color0: "#22c55e" });
    await i18n.changeLanguage("en");
  });
});
