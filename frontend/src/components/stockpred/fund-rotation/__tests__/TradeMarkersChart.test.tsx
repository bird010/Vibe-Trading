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

  it("mutes a later trade when its current and previous signals have the same target", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
          { trade_date: "20250110", open: 10.5, high: 11, low: 10, close: 10.8, vol: 120 },
        ]}
        trades={[
          { trade_date: "20250106", action: "BUY", filled: 10, price: 10.5, target_weight: 0.5 },
          { trade_date: "20250110", action: "SELL", filled: 2, price: 10.8, target_weight: 0.5 },
        ]}
        signals={[
          { date: "20250103", target_weight: 0.5 },
          { date: "20250108", target_weight: 0.5 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.objectContaining({ time: "20250110", muted: true }),
    ]);
  });

  it("accepts production weight signals when finding the previous target", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
          { trade_date: "20250113", open: 10.5, high: 11, low: 10, close: 10.8, vol: 120 },
        ]}
        trades={[
          { trade_date: "20250106", signal_date: "20250103", action: "BUY", filled: 10, price: 10.5 },
          { trade_date: "20250113", signal_date: "20250110", action: "SELL", filled: 2, price: 10.8, target_weight: 0.5 },
        ]}
        signals={[
          { week_ending: "20250103", weight: 0.5 },
          { week_ending: "20250110", weight: 0.5 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.objectContaining({ time: "20250113", muted: true }),
    ]);
  });

  it("does not mute a changed trade just because it matches its current signal", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
          { trade_date: "20250113", open: 10.5, high: 11, low: 10, close: 10.8, vol: 120 },
        ]}
        trades={[
          { trade_date: "20250106", signal_date: "20250103", action: "BUY", filled: 10, price: 10.5, target_weight: 0.5 },
          { trade_date: "20250113", signal_date: "20250110", action: "SELL", filled: 2, price: 10.8, target_weight: 0.6 },
        ]}
        signals={[
          { week_ending: "20250103", target_weight: 0.5 },
          { week_ending: "20250110", target_weight: 0.6 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
    ]);
  });

  it("uses the production signal_week to avoid treating a changed target as unchanged", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
          { trade_date: "20250113", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
        ]}
        trades={[
          { trade_date: "20250106", signal_week: "20241227", action: "BUY", filled: 10, price: 10.5, target_weight: 0.5 },
          { trade_date: "20250113", signal_week: "20250103", action: "BUY", filled: 10, price: 10.5, target_weight: 0.6 },
        ]}
        signals={[
          { week_ending: "20241227", weight: 0.5 },
          { week_ending: "20250103", weight: 0.5 },
          { week_ending: "20250110", weight: 0.6 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
    ]);
  });

  it("advances the processed target within the same signal", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
          { trade_date: "20250113", open: 10.5, high: 11, low: 10, close: 10.8, vol: 120 },
          { trade_date: "20250114", open: 10.8, high: 11.2, low: 10.4, close: 11, vol: 110 },
        ]}
        trades={[
          { trade_date: "20250106", signal_date: "20250103", action: "BUY", filled: 10, price: 10.5, target_weight: 0.5 },
          { trade_date: "20250113", signal_date: "20250110", action: "SELL", filled: 2, price: 10.8, target_weight: 0.6 },
          { trade_date: "20250114", signal_date: "20250110", action: "BUY", filled: 1, price: 11, target_weight: 0.6 },
        ]}
        signals={[
          { date: "20250103", weight: 0.5 },
          { date: "20250110", weight: 0.6 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
      expect.objectContaining({ time: "20250114", muted: true }),
    ]);
  });

  it("mutes a same-date trade after the first trade marker", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
        ]}
        trades={[
          { trade_date: "20250106", signal_date: "20250106", action: "BUY", filled: 10, price: 10.5, target_weight: 0.5 },
          { trade_date: "20250106", signal_date: "20250106", action: "SELL", filled: 2, price: 10.5, target_weight: 0.5 },
        ]}
        signals={[
          { date: "20250103", target_weight: 0.5 },
          { date: "20250106", target_weight: 0.5 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.objectContaining({ muted: true }),
    ]);
  });

  it("does not let null, empty, or boolean targets replace the previous weight", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
          { trade_date: "20250107", open: 10.5, high: 11, low: 10, close: 10.8, vol: 120 },
          { trade_date: "20250108", open: 10.8, high: 11.2, low: 10.4, close: 11, vol: 110 },
          { trade_date: "20250109", open: 11, high: 11.3, low: 10.7, close: 11.1, vol: 105 },
          { trade_date: "20250110", open: 11.1, high: 11.4, low: 10.8, close: 11.2, vol: 115 },
        ]}
        trades={[
          { trade_date: "20250106", signal_date: "20250105", action: "BUY", filled: 10, price: 10.5, target_weight: 0.5 },
          { trade_date: "20250107", signal_date: "20250105", action: "SELL", filled: 2, price: 10.8, target_weight: null as unknown as number },
          { trade_date: "20250108", signal_date: "20250105", action: "BUY", filled: 5, price: 11, target_weight: "" as unknown as number },
          { trade_date: "20250109", signal_date: "20250105", action: "SELL", filled: 2, price: 11.1, target_weight: false as unknown as number },
          { trade_date: "20250110", signal_date: "20250105", action: "BUY", filled: 5, price: 11.2, target_weight: 0.5 },
        ]}
        signals={[
          { date: "20250103", target_weight: 0.5 },
          { date: "20250104", target_weight: null as unknown as number },
          { date: "20250104", target_weight: "" as unknown as number },
          { date: "20250104", target_weight: false as unknown as number },
          { date: "20250105", target_weight: 0.5 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
      expect.objectContaining({ muted: true }),
    ]);
  });

  it("keeps the previous target weight across an explicit undefined trade target", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
          { trade_date: "20250107", open: 10.5, high: 11, low: 10, close: 10.8, vol: 120 },
          { trade_date: "20250108", open: 10.8, high: 11.2, low: 10.4, close: 11, vol: 110 },
        ]}
        trades={[
          { trade_date: "20250106", signal_date: "20250105", action: "BUY", filled: 10, price: 10.5, target_weight: 0.5 },
          { trade_date: "20250107", signal_date: "20250105", action: "SELL", filled: 2, price: 10.8, target_weight: undefined },
          { trade_date: "20250108", signal_date: "20250105", action: "BUY", filled: 5, price: 11, target_weight: 0.5 },
        ]}
        signals={[
          { date: "20250103", target_weight: 0.5 },
          { date: "20250105", target_weight: 0.5 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
      expect.objectContaining({ time: "20250108", muted: true }),
    ]);
  });

  it("does not mute first, changed-weight, or missing-target trades", () => {
    render(
      <TradeMarkersChart
        ohlcv={[
          { trade_date: "20250106", open: 10, high: 11, low: 9, close: 10.5, vol: 100 },
          { trade_date: "20250110", open: 10.5, high: 11, low: 10, close: 10.8, vol: 120 },
          { trade_date: "20250113", open: 10.8, high: 11.2, low: 10.4, close: 11, vol: 110 },
        ]}
        trades={[
          { trade_date: "20250106", action: "BUY", filled: 10, price: 10.5, target_weight: 0.5 },
          { trade_date: "20250110", action: "SELL", filled: 2, price: 10.8, target_weight: 0.6 },
          { trade_date: "20250113", action: "BUY", filled: 5, price: 11 },
        ]}
        tsCode="159712.SZ"
      />,
    );

    expect(chartMock.props?.markers).toEqual([
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
      expect.not.objectContaining({ muted: true }),
    ]);
  });
});
