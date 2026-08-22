import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type {
  CandidatePoolResponse,
  ComparisonEquityData,
  InstrumentChartResponse,
} from "../types";
const chartMock = vi.hoisted(() => ({
  options: null as Record<string, any> | null,
  handlers: {} as Record<string, (params: Record<string, any>) => void>,
}));

vi.mock("@/lib/echarts", () => ({
  echarts: {
    init: () => ({
      setOption: (options: Record<string, any>) => {
        if (!chartMock.options) {
          chartMock.options = options;
          return;
        }
        const existingSeries = Array.isArray(chartMock.options.series)
          ? chartMock.options.series
          : [];
        const nextSeries = Array.isArray(options.series)
          ? options.series.map((patch: Record<string, any>) => {
              const existing = existingSeries.find(
                (entry: Record<string, any>) => entry.id === patch.id,
              );
              return existing
                ? {
                    ...existing,
                    ...patch,
                    lineStyle: { ...existing.lineStyle, ...patch.lineStyle },
                    areaStyle: { ...existing.areaStyle, ...patch.areaStyle },
                  }
                : patch;
            })
          : existingSeries;
        chartMock.options = { ...chartMock.options, ...options, series: nextSeries };
      },
      on: (event: string, handler: (params: Record<string, any>) => void) => {
        chartMock.handlers[event] = handler;
      },
      off: (event: string) => {
        delete chartMock.handlers[event];
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

function representative(selected_code: string) {
  return {
    cluster_id: 1,
    cluster_size: 1,
    selected_code,
    selected_name: null,
    selected_fund_type: null,
    lock_maintained: false,
    exclusion_reason: "",
  };
}

const candidatePoolWithRepresentatives: CandidatePoolResponse = {
  ...candidatePool,
  reclusters: [
    {
      ...candidatePool.reclusters[0],
      representatives: [representative("159001.SZ")],
    },
    {
      ...candidatePool.reclusters[1],
      representatives: [representative("510300.SH")],
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
    chartMock.handlers = {};
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

  it("filters the selected interval to its representative fund and markers", () => {
    const model = buildClusterIntervalChartModel({
      equity,
      candidatePool: candidatePoolWithRepresentatives,
      selectedIntervalIndex: 0,
      charts: {
        "159001.SZ": chart("159001.SZ", [
          { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
          { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
          { trade_date: "20250110", open: 12, high: 14, low: 11, close: 13, vol: 1 },
          { trade_date: "20250113", open: 13, high: 15, low: 12, close: 14, vol: 1 },
        ], [
          { trade_date: "20250106", action: "BUY", status: "FILLED", filled: 1, price: 11 },
          { trade_date: "20250113", action: "SELL", status: "REJECTED", filled: 0, price: 14 },
        ]),
        "510300.SH": chart("510300.SH", [
          { trade_date: "20250103", open: 19, high: 21, low: 18, close: 20, vol: 1 },
          { trade_date: "20250106", open: 20, high: 22, low: 19, close: 21, vol: 1 },
          { trade_date: "20250110", open: 21, high: 23, low: 20, close: 22, vol: 1 },
        ], [
          { trade_date: "20250106", action: "SELL", status: "FILLED", filled: 1, price: 21 },
        ]),
        "000000.SZ": chart("000000.SZ", [
          { trade_date: "20250103", open: 29, high: 31, low: 28, close: 30, vol: 1 },
          { trade_date: "20250106", open: 30, high: 32, low: 29, close: 31, vol: 1 },
        ], [
          { trade_date: "20250106", action: "BUY", status: "FILLED", filled: 1, price: 31 },
        ]),
      },
    });

    expect(model.series.map(({ id }) => id)).toEqual([
      "equity:0",
      "fund:159001.SZ:0",
    ]);
    expect(model.series.flatMap(({ data }) => data).every(([date]) => date <= "20250109")).toBe(true);
    expect(model.markPoints).toEqual([
      expect.objectContaining({
        instrument: "159001.SZ",
        intervalIndex: 0,
        coord: ["20250106", 1.1],
      }),
    ]);
    expect(model.markPoints.some(({ instrument }) => instrument !== "159001.SZ")).toBe(false);
  });

  it("builds actual fund weights and cash to a 100% stack", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ"), representative("BBB.SZ")],
      }],
    };
    const model = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250102", "20250103"],
        series: { strategy: [100, 101] },
      },
      candidatePool: pool,
      selectedIntervalIndex: 0,
      charts: {
        "AAA.SZ": {
          ...chart("AAA.SZ", [
            { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
          ]),
          positions: [
            { trade_date: "20250102", actual_weight: 0.5 },
            { trade_date: "20250103", actual_weight: 0.4 },
          ],
        },
        "BBB.SZ": {
          ...chart("BBB.SZ", [
            { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
          ]),
          positions: [
            { trade_date: "20250102", actual_weight: 0.3 },
            { trade_date: "20250103", actual_weight: 0.2 },
          ],
        },
      },
    });

    expect(model.positionSeries).toEqual([
      expect.objectContaining({ logicalId: "fund:AAA.SZ", stack: "positions" }),
      expect.objectContaining({ logicalId: "fund:BBB.SZ", stack: "positions" }),
      expect.objectContaining({ logicalId: "cash", stack: "positions" }),
    ]);
    expect(model.positionSeries.find((series) => series.logicalId === "cash")?.data[0]?.[1]).toBeCloseTo(0.2);
    expect(model.positionSeries.find((series) => series.logicalId === "cash")?.data[1]?.[1]).toBeCloseTo(0.4);
  });

  it("forwards actual weights, sets sold funds to zero, and preserves gaps", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ"), representative("BBB.SZ")],
      }],
    };
    const model = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250102", "20250103", "20250104"],
        series: { strategy: [100, 101, 102] },
      },
      candidatePool: pool,
      selectedIntervalIndex: 0,
      charts: {
        "AAA.SZ": {
          ...chart("AAA.SZ", [
            { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250104", open: 1, high: 1, low: 1, close: 1, vol: 1 },
          ], [
            { trade_date: "20250103", action: "SELL", status: "FILLED", filled: 1, price: 1, target_weight: 0, post_holding: 0 },
          ]),
          positions: [{ trade_date: "20250102", actual_weight: 0.5 }],
        },
        "BBB.SZ": {
          ...chart("BBB.SZ", [
            { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250104", open: 1, high: 1, low: 1, close: 1, vol: 1 },
          ]),
          positions: [{ trade_date: "20250103", actual_weight: 0.25 }],
        },
      },
    });

    const positionData = (logicalId: string) =>
      model.positionSeries.find((series) => series.logicalId === logicalId)?.data;
    expect(positionData("fund:AAA.SZ")).toEqual([
      ["20250102", 0.5],
      ["20250103", 0],
      ["20250104", 0],
    ]);
    expect(positionData("fund:BBB.SZ")).toEqual([
      ["20250102", null],
      ["20250103", 0.25],
      ["20250104", 0.25],
    ]);
  });

  it("keeps the actual position and cash after a partial sell targeting zero", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ")],
      }],
    };
    const model = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250102", "20250103", "20250104"],
        series: { strategy: [100, 101, 102] },
      },
      candidatePool: pool,
      selectedIntervalIndex: 0,
      charts: {
        "AAA.SZ": {
          ...chart("AAA.SZ", [
            { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250104", open: 1, high: 1, low: 1, close: 1, vol: 1 },
          ], [
            {
              trade_date: "20250103",
              action: "SELL",
              status: "PARTIAL",
              filled: 1,
              price: 1,
              target_weight: 0,
              post_holding: 10,
            },
          ]),
          positions: [{ trade_date: "20250102", actual_weight: 0.5 }],
        },
      },
    });

    expect(model.positionSeries.find((series) => series.logicalId === "fund:AAA.SZ")?.data).toEqual([
      ["20250102", 0.5],
      ["20250103", 0.5],
      ["20250104", 0.5],
    ]);
    expect(model.positionSeries.find((series) => series.logicalId === "cash")?.data).toEqual([
      ["20250102", 0.5],
      ["20250103", 0.5],
      ["20250104", 0.5],
    ]);
  });

  it("shows a null gap for an explicit invalid actual weight record", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ")],
      }],
    };
    const model = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250102", "20250103", "20250104"],
        series: { strategy: [100, 101, 102] },
      },
      candidatePool: pool,
      selectedIntervalIndex: 0,
      charts: {
        "AAA.SZ": {
          ...chart("AAA.SZ", [
            { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250104", open: 1, high: 1, low: 1, close: 1, vol: 1 },
          ]),
          positions: [
            { trade_date: "20250102", actual_weight: 0.5 },
            { trade_date: "20250103", actual_weight: null },
            { trade_date: "20250104", actual_weight: 0.25 },
          ],
        },
      },
    });

    expect(model.positionSeries.find((series) => series.logicalId === "fund:AAA.SZ")?.data).toEqual([
      ["20250102", 0.5],
      ["20250103", null],
      ["20250104", 0.25],
    ]);
    expect(model.positionSeries.find((series) => series.logicalId === "cash")?.data).toEqual([
      ["20250102", 0.5],
      ["20250103", null],
      ["20250104", 0.75],
    ]);
  });

  it("uses a completed sell after an invalid record as zero weight", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ")],
      }],
    };
    const model = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250102", "20250103", "20250104"],
        series: { strategy: [100, 101, 102] },
      },
      candidatePool: pool,
      selectedIntervalIndex: 0,
      charts: {
        "AAA.SZ": {
          ...chart("AAA.SZ", [
            { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250104", open: 1, high: 1, low: 1, close: 1, vol: 1 },
          ], [
            { trade_date: "20250104", action: "SELL", status: "FILLED", filled: 1, price: 1, post_holding: 0 },
          ]),
          positions: [
            { trade_date: "20250102", actual_weight: 0.5 },
            { trade_date: "20250103", actual_weight: null },
          ],
        },
      },
    });

    expect(model.positionSeries.find((series) => series.logicalId === "fund:AAA.SZ")?.data).toEqual([
      ["20250102", 0.5],
      ["20250103", null],
      ["20250104", 0],
    ]);
  });

  it("keeps a later nonzero buy with missing actual weight unknown after a sell", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ")],
      }],
    };
    const model = buildClusterIntervalChartModel({
      equity: {
        dates: ["20250102", "20250103", "20250104"],
        series: { strategy: [100, 101, 102] },
      },
      candidatePool: pool,
      selectedIntervalIndex: 0,
      charts: {
        "AAA.SZ": {
          ...chart("AAA.SZ", [
            { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            { trade_date: "20250104", open: 1, high: 1, low: 1, close: 1, vol: 1 },
          ], [
            { trade_date: "20250103", action: "SELL", status: "FILLED", filled: 1, price: 1, post_holding: 0 },
            { trade_date: "20250104", action: "BUY", status: "FILLED", filled: 1, price: 1, post_holding: 10 },
          ]),
          positions: [{ trade_date: "20250102", actual_weight: 0.5 }],
        },
      },
    });

    expect(model.positionSeries.find((series) => series.logicalId === "fund:AAA.SZ")?.data).toEqual([
      ["20250102", 0.5],
      ["20250103", 0],
      ["20250104", null],
    ]);
    expect(model.positionSeries.find((series) => series.logicalId === "cash")?.data).toEqual([
      ["20250102", 0.5],
      ["20250103", 1],
      ["20250104", null],
    ]);
  });

  it("renders a second 100% grid and bidirectionally highlights paired fund series", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ"), representative("BBB.SZ")],
      }],
    };
    render(
      <ClusterIntervalChart
        equity={{ dates: ["20250102", "20250103"], series: { strategy: [100, 101] } }}
        candidatePool={pool}
        charts={{
          "AAA.SZ": {
            ...chart("AAA.SZ", [
              { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
              { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            ]),
            positions: [{ trade_date: "20250102", actual_weight: 0.5 }],
          },
          "BBB.SZ": {
            ...chart("BBB.SZ", [
              { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
              { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            ]),
            positions: [{ trade_date: "20250102", actual_weight: 0.25 }],
          },
        }}
      />,
    );

    const options = chartMock.options!;
    expect(options.grid).toHaveLength(2);
    expect(options.yAxis[1]).toMatchObject({ min: 0, max: 1 });
    const series = options.series as Array<Record<string, any>>;
    const positionSeries = series.filter((entry) => entry.stack === "positions");
    expect(positionSeries).toEqual(expect.arrayContaining([
      expect.objectContaining({ logicalId: "fund:AAA.SZ", xAxisIndex: 1, yAxisIndex: 1 }),
      expect.objectContaining({ logicalId: "fund:BBB.SZ", xAxisIndex: 1, yAxisIndex: 1 }),
      expect.objectContaining({ logicalId: "cash", xAxisIndex: 1, yAxisIndex: 1 }),
    ]));

    const seriesById = (id: string | undefined) =>
      (chartMock.options?.series as Array<Record<string, any>>).find((entry) => entry.id === id);
    const priceAAA = series.find((entry) => entry.id === "fund:AAA.SZ:0");
    const priceBBB = series.find((entry) => entry.id === "fund:BBB.SZ:0");
    const equitySeries = series.find((entry) => entry.logicalId === "equity");
    const positionAAA = positionSeries.find((entry) => entry.logicalId === "fund:AAA.SZ");
    const positionBBB = positionSeries.find((entry) => entry.logicalId === "fund:BBB.SZ");
    const cash = positionSeries.find((entry) => entry.logicalId === "cash");
    expect(priceAAA?.emphasis).toBeUndefined();
    expect(positionAAA?.emphasis).toBeUndefined();
    const initialStyles = [priceAAA, positionAAA, cash, equitySeries].map((entry) => ({
      id: entry?.id,
      width: entry?.lineStyle.width,
      opacity: entry?.lineStyle.opacity,
    }));
    expect(priceAAA?.lineStyle.color).toBe(positionAAA?.lineStyle.color);
    expect(priceBBB?.lineStyle.color).toBe(positionBBB?.lineStyle.color);

    chartMock.handlers.mouseover?.({ seriesId: priceAAA?.id });
    expect(seriesById(priceAAA?.id)?.lineStyle.opacity).toBe(1);
    expect(seriesById(positionAAA?.id)?.areaStyle.opacity).toBeGreaterThan(seriesById(positionBBB?.id)?.areaStyle.opacity ?? 0);
    expect(seriesById(priceBBB?.id)?.lineStyle.opacity).toBeLessThan(1);
    expect(seriesById(equitySeries?.id)?.lineStyle).toMatchObject({
      width: initialStyles.find((entry) => entry.id === equitySeries?.id)?.width,
      opacity: initialStyles.find((entry) => entry.id === equitySeries?.id)?.opacity,
    });

    chartMock.handlers.mouseover?.({ seriesId: positionBBB?.id });
    expect(seriesById(priceBBB?.id)?.lineStyle.opacity).toBe(1);
    expect(seriesById(positionBBB?.id)?.areaStyle.opacity).toBeGreaterThan(seriesById(positionAAA?.id)?.areaStyle.opacity ?? 0);
    expect(seriesById(equitySeries?.id)?.lineStyle).toMatchObject({
      width: initialStyles.find((entry) => entry.id === equitySeries?.id)?.width,
      opacity: initialStyles.find((entry) => entry.id === equitySeries?.id)?.opacity,
    });

    chartMock.handlers.mouseover?.({ seriesId: cash?.id });
    expect(seriesById(priceAAA?.id)?.lineStyle.opacity).toBeLessThan(1);
    expect(seriesById(priceBBB?.id)?.lineStyle.opacity).toBeLessThan(1);
    expect(seriesById(cash?.id)?.areaStyle.opacity).toBeGreaterThan(seriesById(positionAAA?.id)?.areaStyle.opacity ?? 0);
    expect(seriesById(equitySeries?.id)?.lineStyle).toMatchObject({
      width: initialStyles.find((entry) => entry.id === equitySeries?.id)?.width,
      opacity: initialStyles.find((entry) => entry.id === equitySeries?.id)?.opacity,
    });

    const beforeEquityHover = [priceAAA, priceBBB, cash].map((entry) => ({
      id: entry?.id,
      width: seriesById(entry?.id)?.lineStyle.width,
      opacity: seriesById(entry?.id)?.lineStyle.opacity,
    }));
    chartMock.handlers.mouseover?.({ seriesId: equitySeries?.id });
    for (const beforeStyle of beforeEquityHover) {
      expect(seriesById(beforeStyle.id)?.lineStyle).toMatchObject({
        width: beforeStyle.width,
        opacity: beforeStyle.opacity,
      });
    }
    expect(seriesById(equitySeries?.id)?.lineStyle).toMatchObject({
      width: initialStyles.find((entry) => entry.id === equitySeries?.id)?.width,
      opacity: initialStyles.find((entry) => entry.id === equitySeries?.id)?.opacity,
    });

    chartMock.handlers.mouseout?.({ seriesId: cash?.id });
    for (const initialStyle of initialStyles) {
      expect(seriesById(initialStyle.id)?.lineStyle).toMatchObject({
        width: initialStyle.width,
        opacity: initialStyle.opacity,
      });
    }
    expect(seriesById(cash?.id)?.areaStyle.opacity).toBeGreaterThan(0);
  });

  it("formats the lower position tooltip with all funds, cash, interval, and total", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ"), representative("BBB.SZ")],
      }],
    };
    render(
      <ClusterIntervalChart
        equity={{ dates: ["20250102"], series: { strategy: [100] } }}
        candidatePool={pool}
        charts={{
          "AAA.SZ": {
            ...chart("AAA.SZ", [
              { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            ]),
            name: "甲基金",
            positions: [{ trade_date: "20250102", actual_weight: 0.5 }],
          },
          "BBB.SZ": {
            ...chart("BBB.SZ", [
              { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            ]),
            name: "乙基金",
            positions: [{ trade_date: "20250102", actual_weight: 0.25 }],
          },
        }}
      />,
    );

    const positionSeries = (chartMock.options?.series as Array<Record<string, any>>)
      .filter((entry) => entry.stack === "positions");
    const tooltip = chartMock.options?.tooltip.formatter(
      positionSeries.map((entry) => ({
        axisValue: "20250102",
        marker: "•",
        seriesId: entry.id,
        seriesName: entry.name,
        value: ["20250102", entry.data[0][1]],
      })),
    );

    expect(tooltip).toContain("区间：1");
    expect(tooltip).toContain("甲基金 (AAA.SZ)");
    expect(tooltip).toContain("50.00%");
    expect(tooltip).toContain("乙基金 (BBB.SZ)");
    expect(tooltip).toContain("现金");
    expect(tooltip).toContain("25.00%");
    expect(tooltip).toContain("合计：100.00%");
  });

  it("marks missing lower position components without claiming a full total", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [{
        ...candidatePool.reclusters[0],
        week: "20250102",
        representatives: [representative("AAA.SZ"), representative("BBB.SZ")],
      }],
    };
    render(
      <ClusterIntervalChart
        equity={{ dates: ["20250102", "20250103"], series: { strategy: [100, 101] } }}
        candidatePool={pool}
        charts={{
          "AAA.SZ": {
            ...chart("AAA.SZ", [
              { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
              { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            ]),
            positions: [{ trade_date: "20250102", actual_weight: 0.5 }],
          },
          "BBB.SZ": {
            ...chart("BBB.SZ", [
              { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
              { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            ]),
            positions: [{ trade_date: "20250103", actual_weight: 0.25 }],
          },
        }}
      />,
    );

    const positionSeries = (chartMock.options?.series as Array<Record<string, any>>)
      .filter((entry) => entry.stack === "positions");
    const tooltip = chartMock.options?.tooltip.formatter(
      positionSeries.map((entry) => ({
        axisValue: "20250102",
        marker: "•",
        seriesId: entry.id,
        seriesName: entry.name,
        value: ["20250102", entry.data[0][1]],
      })),
    );

    expect(tooltip).toContain("缺失");
    expect(tooltip).not.toContain("合计：100.00%");
  });

  it("attaches cluster boundaries to the lower grid position series", () => {
    const pool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [
        {
          ...candidatePool.reclusters[0],
          week: "20250102",
          representatives: [representative("AAA.SZ")],
        },
        {
          ...candidatePool.reclusters[1],
          week: "20250103",
          representatives: [representative("AAA.SZ")],
        },
      ],
    };
    render(
      <ClusterIntervalChart
        equity={{ dates: ["20250102", "20250103"], series: { strategy: [100, 101] } }}
        candidatePool={pool}
        charts={{
          "AAA.SZ": {
            ...chart("AAA.SZ", [
              { trade_date: "20250102", open: 1, high: 1, low: 1, close: 1, vol: 1 },
              { trade_date: "20250103", open: 1, high: 1, low: 1, close: 1, vol: 1 },
            ]),
            positions: [{ trade_date: "20250103", actual_weight: 0.5 }],
          },
        }}
      />,
    );

    const lowerSeries = (chartMock.options?.series as Array<Record<string, any>>)
      .filter((entry) => entry.stack === "positions");
    expect(lowerSeries).toEqual(expect.arrayContaining([
      expect.objectContaining({
        xAxisIndex: 1,
        yAxisIndex: 1,
        markLine: expect.objectContaining({
          data: [expect.objectContaining({ name: expect.stringContaining("重聚类") })],
        }),
      }),
    ]));
  });

  it("includes fund name and before/after weights in trade marker tooltips", () => {
    const instrumentChart = {
      ...chart("159001.SZ", [
        { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
        { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
        { trade_date: "20250109", open: 11, high: 13, low: 10, close: 12, vol: 1 },
      ], [
        { trade_date: "20250106", signal_date: "20250103", action: "BUY", status: "FILLED", filled: 10, price: 11, target_weight: 0.25 },
        { trade_date: "20250109", signal_date: "", signal_week: "20250103", action: "SELL", status: "FILLED", filled: 2, price: 12, target_weight: 0.5 },
      ]),
      name: "示例基金",
      signals: [
        { date: "20250103", target_weight: 0.25 },
        { date: "20250108", target_weight: 0.5 },
      ],
    };
    const model = buildClusterIntervalChartModel({
      equity: null,
      candidatePool: { run_id: "run-1", reclusters: [] },
      charts: { "159001.SZ": instrumentChart },
    });

    expect(model.markPoints).toEqual([
      expect.objectContaining({
        fundName: "示例基金 (159001.SZ)",
        beforeWeight: null,
        afterWeight: 0.25,
      }),
      expect.objectContaining({
        fundName: "示例基金 (159001.SZ)",
        beforeWeight: null,
        afterWeight: 0.5,
      }),
    ]);

    render(
      <ClusterIntervalChart
        equity={null}
        candidatePool={{
          run_id: "run-1",
          reclusters: [{
            ...candidatePool.reclusters[0],
            representatives: [representative("159001.SZ")],
          }],
        }}
        charts={{ "159001.SZ": instrumentChart }}
      />,
    );
    const mark = (chartMock.options?.series as Array<Record<string, any>>)[0]?.markPoint.data[0];
    expect((chartMock.options?.series as Array<Record<string, any>>)[0]?.markPoint.tooltip.trigger).toBe("item");
    const tooltip = (chartMock.options?.series as Array<Record<string, any>>)[0]?.markPoint.tooltip.formatter({ data: mark });
    expect(tooltip).toContain("基金名称：示例基金 (159001.SZ)");
    expect(tooltip).toContain("交易前权重：—");
    expect(tooltip).toContain("交易后权重：25.00%");
    const axisTooltip = chartMock.options?.tooltip.formatter({ data: mark });
    expect(axisTooltip).toContain("基金名称：示例基金 (159001.SZ)");
  });

  it("uses zero actual weight after a completed sell before a later buy", () => {
    const instrumentChart = {
      ...chart("510300.SH", [
        { trade_date: "20171110", open: 4, high: 4, low: 4, close: 4, vol: 1 },
        { trade_date: "20171120", open: 4, high: 4, low: 4, close: 4, vol: 1 },
      ], [
        { trade_date: "20171113", action: "SELL", status: "FILLED", filled: 84315, price: 4, target_weight: 0, post_holding: 0 },
        { trade_date: "20171120", action: "BUY", status: "FILLED", filled: 82800, price: 4, target_weight: 1 / 3, post_holding: 82800 },
      ]),
      signals: [
        { date: "20171103", target_weight: 1 / 3 },
        { date: "20171117", target_weight: 1 / 3 },
      ],
      positions: [
        { trade_date: "20171110", actual_weight: 1 / 3 },
      ],
    };

    const model = buildClusterIntervalChartModel({
      equity: null,
      candidatePool: { run_id: "run-1", reclusters: [] },
      charts: { "510300.SH": instrumentChart },
    });

    expect(model.markPoints).toEqual([
      expect.objectContaining({ instrument: "510300.SH", beforeWeight: 0, afterWeight: 1 / 3, muted: false }),
    ]);
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

    expect(chartMock.options?.legend.data).toEqual(["组合收益"]);
    expect(
      (chartMock.options?.series as Array<{ name: string }>)
        .filter((series) => series.name === "沪深300ETF (159001.SZ)"),
    ).toHaveLength(0);
  });

  it("defaults to the latest interval and switches the accessible interval selector", () => {
    render(
      <ClusterIntervalChart
        equity={equity}
        candidatePool={candidatePoolWithRepresentatives}
        charts={{
          "159001.SZ": chart("159001.SZ", [
            { trade_date: "20250103", open: 9, high: 11, low: 8, close: 10, vol: 1 },
            { trade_date: "20250106", open: 10, high: 12, low: 9, close: 11, vol: 1 },
            { trade_date: "20250110", open: 12, high: 14, low: 11, close: 13, vol: 1 },
            { trade_date: "20250113", open: 13, high: 15, low: 12, close: 14, vol: 1 },
          ]),
          "510300.SH": chart("510300.SH", [
            { trade_date: "20250110", open: 19, high: 21, low: 18, close: 20, vol: 1 },
            { trade_date: "20250113", open: 20, high: 23, low: 19, close: 22, vol: 1 },
          ]),
        }}
      />,
    );

    const selector = screen.getByRole("combobox", { name: "选择聚类区间" });
    expect(selector).toHaveValue("1");
    expect(screen.getByRole("option", { name: /重聚类.*2025-01-03/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /重聚类.*2025-01-10/ })).toBeInTheDocument();
    expect((chartMock.options?.series as Array<{ id: string }>).map(({ id }) => id)).toEqual([
      "equity:1",
      "fund:510300.SH:1",
    ]);

    fireEvent.change(selector, { target: { value: "0" } });

    expect(selector).toHaveValue("0");
    expect((chartMock.options?.series as Array<{ id: string }>).map(({ id }) => id)).toEqual([
      "equity:0",
      "fund:159001.SZ:0",
    ]);
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
        candidatePool={candidatePoolWithRepresentatives}
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

  it("renders a clear empty state when no interval exists", () => {
    render(
      <ClusterIntervalChart
        equity={null}
        candidatePool={{ run_id: "run-1", reclusters: [] }}
        charts={{}}
      />,
    );

    expect(screen.getByText("当前没有可用的聚类区间。")).toBeInTheDocument();
  });

  it("keeps the portfolio curve visible when the selected interval has no representatives", () => {
    render(
      <ClusterIntervalChart
        equity={equity}
        candidatePool={{ run_id: "run-1", reclusters: [] }}
        charts={{}}
      />,
    );

    expect(screen.getByText("当前区间没有可用的代表基金，以下仅展示组合收益。")).toBeInTheDocument();
    expect((chartMock.options?.series as Array<{ id: string }>).map(({ id }) => id)).toEqual([
      "equity:0",
    ]);
  });

  it("maps markArea ends to real category dates and keeps marker status colors prioritized", () => {
    const weekendCandidatePool: CandidatePoolResponse = {
      run_id: "run-1",
      reclusters: [
        {
          ...candidatePool.reclusters[0],
          week: "20250103",
          representatives: [representative("159001.SZ")],
        },
        {
          ...candidatePool.reclusters[1],
          week: "20250113",
          representatives: [representative("159001.SZ")],
        },
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
    const xAxisDates = options?.xAxis[0].data as string[];
    const equitySeries = (options?.series as Array<Record<string, any>>).find((series) => series.id === "equity:1");
    const areaData = equitySeries?.markArea.data as Array<Array<{ xAxis: string }>>;
    expect(areaData.flat().every(({ xAxis }) => xAxisDates.includes(xAxis))).toBe(true);

    const secondFundSeries = (options?.series as Array<Record<string, any>>).find((series) => series.id === "fund:159001.SZ:1");
    expect(secondFundSeries?.markPoint.data).toEqual([
      expect.objectContaining({ value: "B", name: "BUY · PARTIAL", itemStyle: { color: "#222222" } }),
      expect.objectContaining({ value: "S", name: "SELL · REJECTED", itemStyle: { color: "#111111" } }),
    ]);

    fireEvent.change(screen.getByRole("combobox", { name: "选择聚类区间" }), {
      target: { value: "0" },
    });
    const firstFundSeries = (chartMock.options?.series as Array<Record<string, any>>).find((series) => series.id === "fund:159001.SZ:0");
    expect(firstFundSeries?.markPoint.data).toEqual([
      expect.objectContaining({ value: "B", itemStyle: { color: "#333333" } }),
      expect.objectContaining({ value: "S", itemStyle: { color: "#44444480" } }),
    ]);
  });
});
