import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  TradeMarkersChart,
  type OHLCVBar,
  type TradeMarker,
} from "../TradeMarkersChart";

function dateAt(offset: number): string {
  const date = new Date(Date.UTC(2024, 0, 1 + offset));
  return date.toISOString().slice(0, 10).replaceAll("-", "");
}

function bars(count: number): OHLCVBar[] {
  return Array.from({ length: count }, (_, index) => {
    const price = 3 + index * 0.001;
    return {
      trade_date: dateAt(index),
      open: price,
      high: price + 0.02,
      low: price - 0.02,
      close: price + 0.01,
      vol: 1_000_000,
    };
  });
}

describe("TradeMarkersChart", () => {
  it("normalizes numeric dates and centers the window on the latest trade", () => {
    const ohlcv = bars(250);
    const tradeDate = ohlcv[40].trade_date;
    const trade = {
      trade_date: Number(tradeDate),
      action: "BUY",
      status: "FILLED",
      filled: 100,
      price: ohlcv[40].close,
      reason: "rebalance",
    } as unknown as TradeMarker;

    const { container } = render(
      <TradeMarkersChart
        ohlcv={ohlcv}
        trades={[trade]}
        signals={[]}
        tsCode="510300.SH"
      />,
    );

    expect(
      screen.getByText("当前窗口显示 1/1 笔成交、0/0 条信号"),
    ).toBeDefined();
    expect(container.querySelector("polygon.fill-emerald-700")).not.toBeNull();
    expect(screen.getByText(tradeDate)).toBeDefined();
  });
});
