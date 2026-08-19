import { render } from "@testing-library/react";
import { TradeMarkersChart } from "../TradeMarkersChart";

const chartMock = vi.hoisted(() => ({
  props: null as Record<string, unknown> | null,
}));

vi.mock("@/components/charts/CandlestickChart", () => ({
  CandlestickChart: (props: Record<string, unknown>) => {
    chartMock.props = props;
    return null;
  },
}));

const OHLCV = [
  { trade_date: "20250103", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
  { trade_date: "20250106", open: 10.5, high: 12, low: 10, close: 11, vol: 120 },
];

const TRADES = [
  { trade_date: "20250106", action: "BUY" as const, filled: 10, price: 11 },
];

describe("TradeMarkersChart", () => {
  it("keeps transformed chart data stable across unrelated rerenders", () => {
    const props = {
      ohlcv: OHLCV,
      trades: TRADES,
      signals: [],
      tsCode: "159712.SZ",
      dateRange: { start: "20250101", end: "20250131" },
    };
    const { rerender } = render(<TradeMarkersChart {...props} />);
    const firstData = chartMock.props?.data;
    const firstMarkers = chartMock.props?.markers;

    rerender(<TradeMarkersChart {...props} />);

    expect(chartMock.props?.data).toBe(firstData);
    expect(chartMock.props?.markers).toBe(firstMarkers);
  });

  it("passes backend weekly Strategy Score points to the shared Kline chart", () => {
    render(
      <TradeMarkersChart
        ohlcv={OHLCV}
        trades={TRADES}
        tsCode="159712.SZ"
        strategyScore={{
            id: "primary_score",
            label: "策略得分（周频）",
            frequency: "WEEKLY",
            direction: "HIGHER_BETTER",
            scope: "CLUSTER",
            subject_id: "cluster:3",
            model_id: "cluster_momentum",
            model_version: "1",
            points: [
              { date: "20250103", value: 0.8, eligible: true },
              { date: "20250106", value: 0.82, eligible: true },
            ],
          }}
      />,
    );

    expect(chartMock.props?.strategyScore).toEqual({
      "策略得分（周频）": [
        { time: "20250103", value: 0.8 },
        { time: "20250106", value: 0.82 },
      ],
    });
  });

  it("keeps ineligible finite points and uses backend frequency metadata", () => {
    render(
      <TradeMarkersChart
        ohlcv={OHLCV}
        trades={TRADES}
        tsCode="159712.SZ"
        strategyScore={{
          id: "primary_score",
          label: "Composite Score",
          frequency: "MONTHLY",
          direction: "HIGHER_BETTER",
          scope: "INSTRUMENT",
          model_id: "multi_factor",
          model_version: "2",
          points: [
            { date: "20250103", value: -0.03, eligible: false },
          ],
        }}
      />,
    );

    expect(chartMock.props?.strategyScore).toEqual({
      "Composite Score": [{ time: "20250103", value: -0.03 }],
    });
  });
});
