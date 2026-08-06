import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import {
  TradeMarkersChart,
  type OHLCVBar,
  type TradeMarker,
} from "../TradeMarkersChart";

const sharedChart = vi.hoisted(() => ({
  props: null as null | {
    data: Array<{
      time: string;
      code?: string;
      open: number;
      high: number;
      low: number;
      close: number;
      volume: number;
    }>;
    markers?: Array<{
      time: string;
      code?: string;
      side: "BUY" | "SELL";
      price: number;
      qty?: number;
      status?: "FILLED" | "PARTIAL" | "REJECTED";
      reason?: string;
    }>;
    height?: number;
  },
}));

vi.mock("@/components/charts/CandlestickChart", () => ({
  CandlestickChart: (props: NonNullable<typeof sharedChart.props>) => {
    sharedChart.props = props;
    return <div data-testid="shared-candlestick-chart" />;
  },
}));

describe("TradeMarkersChart", () => {
  it("adapts fund-rotation evidence to the shared candlestick component", () => {
    const ohlcv = [
      {
        trade_date: 20240108,
        open: 3.4,
        high: 3.6,
        low: 3.3,
        close: 3.5,
        vol: 1_000_000,
      } as unknown as OHLCVBar,
      {
        trade_date: "2024-01-09",
        open: 3.5,
        high: 3.7,
        low: 3.4,
        close: 3.6,
        vol: 1_100_000,
      },
    ];
    const trades: TradeMarker[] = [
      {
        trade_date: "20240108",
        action: "BUY",
        status: "FILLED",
        filled: 100,
        price: 3.5,
        signal_date: "2024-01-05",
        target_weight: 0.25,
        reason: "rebalance",
      },
      {
        trade_date: "20240109",
        action: "SELL",
        status: "PARTIAL",
        filled: 50,
        price: 3.6,
      },
      {
        trade_date: "20240109",
        action: "BUY",
        status: "BLOCKED",
        filled: 0,
        price: 0,
        blocked_reason: "insufficient_adv_history",
      },
    ];

    render(
      <TradeMarkersChart
        ohlcv={ohlcv}
        trades={trades}
        signals={[{ week_ending: "2024-01-05", target_weight: 0.25 }]}
        tsCode="510300.SH"
      />,
    );

    expect(screen.getByTestId("shared-candlestick-chart")).toBeDefined();
    expect(sharedChart.props?.height).toBe(500);
    expect(sharedChart.props?.data).toEqual([
      {
        time: "20240108",
        code: "510300.SH",
        open: 3.4,
        high: 3.6,
        low: 3.3,
        close: 3.5,
        volume: 1_000_000,
      },
      {
        time: "20240109",
        code: "510300.SH",
        open: 3.5,
        high: 3.7,
        low: 3.4,
        close: 3.6,
        volume: 1_100_000,
      },
    ]);
    expect(sharedChart.props?.markers).toEqual([
      expect.objectContaining({
        time: "20240108",
        side: "BUY",
        status: "FILLED",
        qty: 100,
        price: 3.5,
        reason: "rebalance · Signal 20240105 · Target 25.00%",
      }),
      expect.objectContaining({
        time: "20240109",
        side: "SELL",
        status: "PARTIAL",
        qty: 50,
        price: 3.6,
      }),
      expect.objectContaining({
        time: "20240109",
        side: "BUY",
        status: "REJECTED",
        qty: 0,
        price: 3.6,
        reason: "insufficient_adv_history",
      }),
    ]);
    expect(
      screen.getByText("图中显示 3/3 笔成交或阻断；下方记录 1 条目标权重信号"),
    ).toBeDefined();
    expect(screen.getByText("目标权重：25.0%")).toBeDefined();
    expect(screen.getByText("insufficient_adv_history")).toBeDefined();
  });
});
