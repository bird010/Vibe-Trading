import { render } from "@testing-library/react";
import { CandlestickChart } from "../CandlestickChart";
import type { PriceBar, TradeMarker } from "@/lib/api";


const chartMock = vi.hoisted(() => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
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
  beforeEach(() => chartMock.setOption.mockReset());

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
});
