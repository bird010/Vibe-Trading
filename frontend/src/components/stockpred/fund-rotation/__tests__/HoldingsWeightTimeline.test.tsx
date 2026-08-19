import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { HoldingsWeightTimeline } from "../holdings/HoldingsWeightTimeline";
import type { HoldingsTimelineResponse } from "../types";

const data: HoldingsTimelineResponse = {
  run_id: "run-1",
  start_date: "20240102",
  end_date: "20240105",
  instruments: [
    { ts_code: "510300.SH", name: "沪深300ETF" },
    { ts_code: "159915.SZ", name: "创业板ETF" },
  ],
  intervals: [
    {
      ts_code: "510300.SH",
      start_date: "20240102",
      end_date: "20240103",
      actual_weight: 0.5,
      target_weight: 0.5,
    },
    {
      ts_code: "_CASH",
      start_date: "20240104",
      end_date: "20240105",
      actual_weight: 1,
      target_weight: 1,
    },
  ],
  rebalance_markers: [
    {
      signal_date: "20240103",
      changed_positions: 1,
      turnover: 0.5,
      decision_id: "d-1",
    },
  ],
};

describe("HoldingsWeightTimeline", () => {
  it("renders continuous intervals, Cash, and selects a signal marker", () => {
    const onSelectSignalDate = vi.fn();
    render(
      <HoldingsWeightTimeline
        data={data}
        selectedSignalDate={null}
        window={{ start: "20240102", end: "20240105" }}
        onWindowChange={vi.fn()}
        onSelectSignalDate={onSelectSignalDate}
      />,
    );

    expect(screen.getByText("沪深300ETF")).toBeDefined();
    expect(screen.getByText("Cash")).toBeDefined();
    expect(screen.getByTestId("holding-interval-510300.SH-20240102-20240103")).toBeDefined();
    fireEvent.click(screen.getByTestId("holding-marker-20240103"));
    expect(onSelectSignalDate).toHaveBeenCalledWith("20240103");
  });

  it("shows the current semantic zoom mode", () => {
    render(
      <HoldingsWeightTimeline
        data={data}
        selectedSignalDate={null}
        window={{ start: "20240102", end: "20240105" }}
        onWindowChange={vi.fn()}
        onSelectSignalDate={vi.fn()}
      />,
    );
    expect(screen.getByText(/周\/日/)).toBeDefined();
  });

  it("filters low-information markers when zoomed out beyond three years", () => {
    render(
      <HoldingsWeightTimeline
        data={{
          ...data,
          start_date: "20200101",
          end_date: "20250101",
          rebalance_markers: [
            ...data.rebalance_markers,
            { signal_date: "20220101", changed_positions: 0, turnover: 0, decision_id: "d-2" },
          ],
        }}
        selectedSignalDate={null}
        window={{ start: "20200101", end: "20250101" }}
        onWindowChange={vi.fn()}
        onSelectSignalDate={vi.fn()}
      />,
    );
    expect(screen.getByTestId("holding-marker-20240103")).toBeDefined();
    expect(screen.queryByTestId("holding-marker-20220101")).toBeNull();
  });

  it("uses real calendar ticks and promotes holdings active in the zoom window", () => {
    render(
      <HoldingsWeightTimeline
        data={{
          ...data,
          start_date: "20180101",
          end_date: "20250101",
          instruments: [
            { ts_code: "OLD.SH", name: "历史持仓" },
            { ts_code: "NEW.SH", name: "窗口持仓" },
          ],
          intervals: [
            { ts_code: "OLD.SH", start_date: "20180101", end_date: "20190101", actual_weight: 1, target_weight: 1 },
            { ts_code: "NEW.SH", start_date: "20240101", end_date: "20250101", actual_weight: 0.8, target_weight: 0.8 },
          ],
        }}
        selectedSignalDate={null}
        window={{ start: "20240101", end: "20250101" }}
        onWindowChange={vi.fn()}
        onSelectSignalDate={vi.fn()}
      />,
    );

    expect(screen.getByText("窗口持仓")).toBeDefined();
    expect(screen.queryByText("历史持仓")).toBeNull();
    expect(screen.getByText("2024-01")).toBeDefined();
    expect(screen.getByText("2024-04")).toBeDefined();
    expect(screen.queryByText("2024-05")).toBeNull();
  });
});
