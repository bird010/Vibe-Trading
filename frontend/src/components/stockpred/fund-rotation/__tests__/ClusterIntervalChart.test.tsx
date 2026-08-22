import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CandidatePoolResponse,
  ComparisonEquityData,
  InstrumentChartResponse,
} from "../types";
const chartMock = vi.hoisted(() => ({
  options: null as Record<string, any> | null,
}));

vi.mock("@/lib/echarts", () => ({
  echarts: {
    init: () => ({
      setOption: (options: Record<string, any>) => {
        chartMock.options = options;
      },
      resize: vi.fn(),
      dispose: vi.fn(),
    }),
  },
}));

vi.mock("@/lib/chart-theme", () => ({
  getChartTheme: () => ({
    gridColor: "#aaaaaa",
    textColor: "#111111",
    axisColor: "#999999",
    upColor: "#333333",
    downColor: "#444444",
    warningColor: "#222222",
    tooltipBg: "#ffffff",
    tooltipBorder: "#dddddd",
    tooltipText: "#111111",
  }),
}));

import {
  buildClusterIntervalChartModel,
  ClusterIntervalChart,
} from "../ClusterIntervalChart";

const equity: ComparisonEquityData = {
  dates: ["20250103", "20250106", "20250109", "20250110", "20250113"],
  series: { strategy: [100, 110, 120, 130, 143] },
};

const candidatePool: CandidatePoolResponse = {
  run_id: "run-1",
  reclusters: [
    {
      week: "2025-01-03",
      num_etfs: 2,
      overall: "PASS",
      max_cluster_share: null,
      max_cluster_share_status: null,
      effective_cluster_count: null,
      effective_cluster_count_status: null,
      representatives: [],
    },
    {
      week: "20250110.0",
      num_etfs: 2,
      overall: "PASS",
      max_cluster_share: null,
      max_cluster_share_status: null,
      effective_cluster_count: null,
      effective_cluster_count_status: null,
      representatives: [],
    },
  ],
};

function chart(
  tsCode: string,
  ohlcv: InstrumentChartResponse["ohlcv"],
  trades: InstrumentChartResponse["trades"] = [],
): InstrumentChartResponse {
  return {
    ts_code: tsCode,
    run_id: "run-1",
    signals: [
      { date: "20250103", target_weight: 0.5 },
      { date: "20250108", target_weight: 0.5 },
    ],
    trades,
    ohlcv,
    positions: [],
    orders: [],
    ohlcv_source: { available: true },
    mode: "RESEARCH_ONLY",
  };
}

describe("buildClusterIntervalChartModel", () => {
  beforeEach(() => {
    chartMock.options = null;
  });

  it("splits at both recluster dates and normalizes each segment independently", () => {
    const model = buildClusterIntervalChartModel({
      equity,
      candidatePool,
      charts: {
        "159001.SZ": chart("159001.SZ", [
          { trade_date: "2025-01-03", open: 9, high: 11, low: 8, close: 10, vol: 1 },
          { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
          { trade_date: "20250109", open: 11, high: 13, low: 10, close: 12, vol: 1 },
          { trade_date: "20250110", open: 12, high: 14, low: 11, close: 13, vol: 1 },
          { trade_date: "20250113", open: 13, high: 15, low: 12, close: 14, vol: 1 },
        ], [
          { trade_date: "20250106", action: "BUY", status: "FILLED", filled: 10, price: 11, target_weight: 0.5 },
          { trade_date: "20250109", action: "SELL", status: "FILLED", filled: 2, price: 12, target_weight: 0.5 },
          { trade_date: "20250113", action: "SELL", status: "REJECTED", filled: 0, price: 0, target_weight: 0.5 },
        ]),
        "510300.SH": chart("510300.SH", [
          { trade_date: "20250103", open: 0, high: 0, low: 0, close: 0, vol: 1 },
          { trade_date: "20250106", open: 0, high: 0, low: 0, close: Number.NaN, vol: 1 },
          { trade_date: "20250110", open: 19, high: 21, low: 18, close: 20, vol: 1 },
          { trade_date: "20250113", open: 20, high: 23, low: 19, close: 22, vol: 1 },
        ], [
          { trade_date: "20250110", action: "BUY", status: "FILLED", filled: 1, price: 20 },
        ]),
        "000000.SZ": chart("000000.SZ", [
          { trade_date: "20250103", open: 0, high: 0, low: 0, close: 0, vol: 1 },
          { trade_date: "20250110", open: 0, high: 0, low: 0, close: Number.NaN, vol: 1 },
        ]),
      },
    });

    expect(model.intervals).toEqual([
      expect.objectContaining({ index: 0, start: "20250103", end: "20250109" }),
      expect.objectContaining({ index: 1, start: "20250110", end: "20250113" }),
    ]);

    const equitySegments = model.series.filter((series) => series.kind === "equity");
    expect(equitySegments).toHaveLength(2);
    expect(equitySegments[0].data).toEqual([
      ["20250103", 1],
      ["20250106", 1.1],
      ["20250109", 1.2],
    ]);
    expect(equitySegments[1].data).toEqual([
      ["20250110", 1],
      ["20250113", 1.1],
    ]);

    const fundSegments = model.series.filter((series) => series.kind === "fund");
    expect(fundSegments.find((series) => series.instrument === "159001.SZ" && series.intervalIndex === 0)?.data).toEqual([
      ["20250103", 1],
      ["20250106", 1.1],
      ["20250109", 1.2],
    ]);
    expect(fundSegments.find((series) => series.instrument === "159001.SZ" && series.intervalIndex === 1)?.data).toEqual([
      ["20250110", 1],
      ["20250113", 14 / 13],
    ]);
    expect(fundSegments.find((series) => series.instrument === "510300.SH" && series.intervalIndex === 0)).toBeUndefined();
    expect(fundSegments.find((series) => series.instrument === "510300.SH" && series.intervalIndex === 1)?.data).toEqual([
      ["20250110", 1],
      ["20250113", 1.1],
    ]);
    expect(fundSegments.some((series) => series.instrument === "000000.SZ")).toBe(false);
  });

  it("keeps the preceding recluster semantics when visible data starts between boundaries", () => {
    const model = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250108", "20250109", "20250110", "20250113"],
        series: { strategy: [100, 101, 102, 103] },
      },
      candidatePool,
      charts: {},
    });

    expect(model.intervals).toEqual([
      expect.objectContaining({
        index: 0,
        start: "20250108",
        end: "20250109",
        reclusterDate: "20250103",
      }),
      expect.objectContaining({
        index: 1,
        start: "20250110",
        end: "20250113",
        reclusterDate: "20250110",
      }),
    ]);
    expect(model.series[0]?.data).toEqual([
      ["20250108", 1],
      ["20250109", 1.01],
    ]);
  });

  it("binds B/S markers to normalized fund series and preserves muted/status priority", () => {
    const model = buildClusterIntervalChartModel({
      equity,
      candidatePool,
      charts: {
        "159001.SZ": chart("159001.SZ", [
          { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
          { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
          { trade_date: "20250109", open: 11, high: 13, low: 10, close: 12, vol: 1 },
          { trade_date: "20250110", open: 12, high: 14, low: 11, close: 13, vol: 1 },
          { trade_date: "20250113", open: 13, high: 15, low: 12, close: 14, vol: 1 },
        ], [
          { trade_date: "20250106", action: "BUY", status: "FILLED", filled: 10, price: 11, target_weight: 0.5 },
          { trade_date: "20250109", action: "SELL", status: "FILLED", filled: 2, price: 12, target_weight: 0.5 },
          { trade_date: "20250113", action: "SELL", status: "REJECTED", filled: 0, price: 0, target_weight: 0.5 },
        ]),
      },
    });

    expect(model.markPoints).toEqual([
      expect.objectContaining({ seriesId: "fund:159001.SZ:0", value: "B", coord: ["20250106", 1.1], status: "FILLED", muted: false }),
      expect.objectContaining({ seriesId: "fund:159001.SZ:0", value: "S", coord: ["20250109", 1.2], status: "FILLED", muted: true }),
      expect.objectContaining({ seriesId: "fund:159001.SZ:1", value: "S", coord: ["20250113", 14 / 13], status: "REJECTED", muted: true }),
    ]);
  });

  it("places a trade marker on the normalized close while retaining its execution price", () => {
    const model = buildClusterIntervalChartModel({
      equity: null,
      candidatePool: { run_id: "run-1", reclusters: [] },
      charts: {
        "159001.SZ": chart("159001.SZ", [
          { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
          { trade_date: "20250106", open: 10, high: 13, low: 9, close: 12, vol: 1 },
        ], [
          { trade_date: "20250106", action: "BUY", status: "FILLED", filled: 10, price: 10.5 },
        ]),
      },
    });

    expect(model.markPoints).toEqual([
      expect.objectContaining({
        coord: ["20250106", 1.2],
        price: 10.5,
      }),
    ]);
  });

  it("computes muted state from the full fund timeline before slicing intervals", () => {
    const model = buildClusterIntervalChartModel({
      equity,
      candidatePool,
      charts: {
        "159001.SZ": chart("159001.SZ", [
          { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
          { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
          { trade_date: "20250110", open: 12, high: 14, low: 11, close: 13, vol: 1 },
        ], [
          { trade_date: "20250106", action: "BUY", status: "FILLED", filled: 10, price: 11, target_weight: 0.5 },
          { trade_date: "20250110", action: "SELL", status: "FILLED", filled: 2, price: 13, target_weight: 0.5 },
        ]),
      },
    });

    expect(model.markPoints).toEqual([
      expect.objectContaining({ coord: ["20250106", 1.1], muted: false }),
      expect.objectContaining({ coord: ["20250110", 1], muted: true }),
    ]);
  });

  it("uses the common visible end date and filters recluster dates outside it", () => {
    const extendedCandidatePool: CandidatePoolResponse = {
      ...candidatePool,
      reclusters: [
        {
          ...candidatePool.reclusters[0],
          week: "20241230",
        },
        ...candidatePool.reclusters,
        {
          ...candidatePool.reclusters[1],
          week: "20250120",
        },
      ],
    };
    const extendedChart = chart("159001.SZ", [
      { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
      { trade_date: "20250110", open: 12, high: 14, low: 11, close: 13, vol: 1 },
      { trade_date: "20250120", open: 13, high: 15, low: 12, close: 14, vol: 1 },
    ]);
    const model = buildClusterIntervalChartModel({
      equity,
      candidatePool: extendedCandidatePool,
      charts: { "159001.SZ": extendedChart },
    });

    expect(model.intervals).toEqual([
      expect.objectContaining({ start: "20250103", end: "20250109" }),
      expect.objectContaining({ start: "20250110", end: "20250113" }),
    ]);
    expect(model.intervals.some((interval) => interval.start === "20241230" || interval.start === "20250120")).toBe(false);
    expect(model.series.flatMap((series) => series.data).every(([date]) => date <= "20250113")).toBe(true);

    const chartOnlyModel = buildClusterIntervalChartModel({
      equity: null,
      candidatePool: extendedCandidatePool,
      charts: {
        "159001.SZ": chart("159001.SZ", [
          { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
          { trade_date: "20250110", open: 12, high: 14, low: 11, close: 13, vol: 1 },
          { trade_date: "20250120", open: 13, high: 15, low: 12, close: 14, vol: 1 },
        ]),
      },
    });
    expect(chartOnlyModel.intervals.at(-1)).toEqual(
      expect.objectContaining({ start: "20250120", end: "20250120" }),
    );
  });

  it("starts equity normalization at the first non-zero value instead of emitting a fake zero point", () => {
    const finiteModel = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250103", "20250106"],
        series: { strategy: [0, 100] },
      },
      candidatePool: {
        run_id: "run-1",
        reclusters: [],
      },
      charts: {},
    });
    expect(finiteModel.series[0]?.data).toEqual([["20250106", 1]]);
    expect(finiteModel.series[0]?.data.every(([, value]) => Number.isFinite(value))).toBe(true);

    const zeroModel = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250103", "20250106"],
        series: { strategy: [0, 0] },
      },
      candidatePool: {
        run_id: "run-1",
        reclusters: [],
      },
      charts: {},
    });
    expect(zeroModel.series).toEqual([]);
  });

  it("uses stable logical identities, names, colors, and one legend item across intervals", () => {
    const namedChart = {
      ...chart("159001.SZ", [
        { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
        { trade_date: "20250110", open: 12, high: 14, low: 11, close: 13, vol: 1 },
      ]),
      name: "沪深300ETF",
    };
    const model = buildClusterIntervalChartModel({
      equity,
      candidatePool,
      charts: { "159001.SZ": namedChart },
    });
    const equitySegments = model.series.filter((series) => series.kind === "equity");
    const fundSegments = model.series.filter((series) => series.instrument === "159001.SZ");

    expect(new Set(equitySegments.map((series) => series.logicalId))).toEqual(new Set(["equity"]));
    expect(new Set(equitySegments.map((series) => series.name))).toEqual(new Set(["组合收益"]));
    expect(new Set(equitySegments.map((series) => series.color)).size).toBe(1);
    expect(new Set(fundSegments.map((series) => series.logicalId))).toEqual(new Set(["fund:159001.SZ"]));
    expect(new Set(fundSegments.map((series) => series.name))).toEqual(new Set(["沪深300ETF (159001.SZ)"]));
    expect(new Set(fundSegments.map((series) => series.color)).size).toBe(1);

    render(
      <ClusterIntervalChart
        equity={equity}
        candidatePool={candidatePool}
        charts={{ "159001.SZ": namedChart }}
      />,
    );

    expect(chartMock.options?.legend.data).toEqual(["组合收益", "沪深300ETF (159001.SZ)"]);
    expect(
      (chartMock.options?.series as Array<{ name: string }>)
        .filter((series) => series.name === "沪深300ETF (159001.SZ)"),
    ).toHaveLength(2);
  });

  it("clips chart-only data and trade markers to the explicit backtest period", () => {
    const model = buildClusterIntervalChartModel({
      equity: null,
      period: {
        evaluation_start_date: "20250106",
        evaluation_end_date: "20250110",
      },
      candidatePool,
      charts: {
        "159001.SZ": chart("159001.SZ", [
          { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
          { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
          { trade_date: "20250110", open: 11, high: 13, low: 10, close: 12, vol: 1 },
          { trade_date: "20250113", open: 12, high: 14, low: 11, close: 13, vol: 1 },
        ], [
          { trade_date: "20250103", action: "BUY", status: "FILLED", filled: 1, price: 10 },
          { trade_date: "20250110", action: "SELL", status: "FILLED", filled: 1, price: 12 },
          { trade_date: "20250113", action: "SELL", status: "FILLED", filled: 1, price: 13 },
        ]),
      },
    });

    expect(model.intervals).toEqual([
      expect.objectContaining({ start: "20250106", end: "20250109", reclusterDate: "20250103" }),
      expect.objectContaining({ start: "20250110", end: "20250110", reclusterDate: "20250110" }),
    ]);
    expect(model.series.flatMap((series) => series.data).every(([date]) => date >= "20250106" && date <= "20250110")).toBe(true);
    expect(model.markPoints).toEqual([
      expect.objectContaining({ coord: ["20250110", 1] }),
    ]);
  });

  it("renders delayed trades with the shared delayed marker priority and purple color", () => {
    const delayedChart = chart("159001.SZ", [
      { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
      { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
    ], [
      {
        trade_date: "20250106",
        action: "SELL",
        status: "REJECTED",
        filled: 0,
        price: 10.8,
        exit_delay_days: 2,
      },
    ]);
    const model = buildClusterIntervalChartModel({
      equity: null,
      candidatePool: { run_id: "run-1", reclusters: [] },
      charts: { "159001.SZ": delayedChart },
    });

    expect(model.markPoints).toEqual([
      expect.objectContaining({ value: "D", exitDelayDays: 2 }),
    ]);

    render(
      <ClusterIntervalChart
        equity={null}
        candidatePool={{ run_id: "run-1", reclusters: [] }}
        charts={{ "159001.SZ": delayedChart }}
      />,
    );
    const delayedMark = (chartMock.options?.series as Array<Record<string, any>>)[0]?.markPoint.data[0];
    expect(delayedMark).toEqual(expect.objectContaining({
      value: "D",
      itemStyle: { color: "#8b5cf6" },
    }));
    expect(delayedMark.name).toContain("延迟 2 天");
  });

  it("warns when an instrument response reaches the 2000-bar request limit", () => {
    const limitedChart = chart(
      "159001.SZ",
      Array.from({ length: 2000 }, () => ({
        trade_date: "20250103",
        open: 9,
        high: 11,
        low: 8,
        close: 10,
        vol: 1,
      })),
    );

    render(
      <ClusterIntervalChart
        equity={null}
        candidatePool={{ run_id: "run-1", reclusters: [] }}
        charts={{ "159001.SZ": limitedChart }}
      />,
    );

    expect(screen.getByText("行情数据可能被截断")).toBeInTheDocument();
  });

  it("maps markArea ends to real category dates and keeps marker status colors prioritized", () => {
    const weekendCandidatePool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [
        { ...candidatePool.reclusters[0], week: "20250103" },
        { ...candidatePool.reclusters[1], week: "20250113" },
      ],
    };
    const instrumentChart = chart("159001.SZ", [
      { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
      { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
      { trade_date: "20250109", open: 11, high: 13, low: 10, close: 12, vol: 1 },
      { trade_date: "20250113", open: 12, high: 14, low: 11, close: 13, vol: 1 },
      { trade_date: "20250114", open: 13, high: 15, low: 12, close: 14, vol: 1 },
    ], [
      { trade_date: "20250106", action: "BUY", status: "FILLED", filled: 10, price: 11, target_weight: 0.5 },
      { trade_date: "20250109", action: "SELL", status: "FILLED", filled: 2, price: 12, target_weight: 0.5 },
      { trade_date: "20250113", action: "BUY", status: "PARTIAL", filled: 1, price: 13, target_weight: 0.6 },
      { trade_date: "20250114", action: "SELL", status: "REJECTED", filled: 0, price: 0, target_weight: 0.7 },
    ]);

    render(
      <ClusterIntervalChart
        equity={{
          dates: ["20250103", "20250106", "20250109", "20250110", "20250113", "20250114"],
          series: { strategy: [100, 110, 120, 130, 143, 150] },
        }}
        candidatePool={weekendCandidatePool}
        charts={{ "159001.SZ": instrumentChart }}
      />,
    );

    const options = chartMock.options;
    const xAxisDates = options?.xAxis.data as string[];
    const equitySeries = (options?.series as Array<Record<string, any>>).find((series) => series.id === "equity:0");
    const areaData = equitySeries?.markArea.data as Array<Array<{ xAxis: string }>>;
    expect(areaData.flat().every(({ xAxis }) => xAxisDates.includes(xAxis))).toBe(true);

    const fundSeries = (options?.series as Array<Record<string, any>>).find((series) => series.id === "fund:159001.SZ:0");
    expect(fundSeries?.markPoint.data).toEqual([
      expect.objectContaining({ value: "B", itemStyle: { color: "#333333" } }),
      expect.objectContaining({ value: "S", itemStyle: { color: "#44444480" } }),
    ]);
    const secondFundSeries = (options?.series as Array<Record<string, any>>).find((series) => series.id === "fund:159001.SZ:1");
    expect(secondFundSeries?.markPoint.data).toEqual([
      expect.objectContaining({ value: "B", name: "BUY · PARTIAL", itemStyle: { color: "#222222" } }),
      expect.objectContaining({ value: "S", name: "SELL · REJECTED", itemStyle: { color: "#111111" } }),
    ]);
  });
});
