import { useEffect, useMemo, useRef, useState } from "react";
import { getTradeMarkerVisual } from "@/components/charts/CandlestickChart";
import { getChartTheme } from "@/lib/chart-theme";
import { echarts } from "@/lib/echarts";
import { useDarkMode } from "@/hooks/useDarkMode";
import type {
  CandidatePoolResponse,
  BacktestPeriod,
  ComparisonEquityData,
  InstrumentChartResponse,
  InstrumentSignal,
  InstrumentTrade,
} from "./types";
import {
  instrumentTradeExitDelayDays,
  instrumentTradeStatus,
  type InstrumentTradeStatus,
} from "./instrumentTradeMarkers";

export interface ClusterIntervalChartInput {
  equity: ComparisonEquityData | null;
  candidatePool: CandidatePoolResponse | null;
  charts: Record<string, InstrumentChartResponse>;
  period?: BacktestPeriod;
  selectedIntervalIndex?: number;
}

export interface ClusterInterval {
  index: number;
  start: string;
  end: string;
  reclusterDate: string | null;
}

export type ClusterIntervalPoint = [date: string, value: number];
export type ClusterIntervalPositionPoint = [date: string, value: number | null];

export interface ClusterIntervalSeries {
  id: string;
  logicalId: string;
  name: string;
  color: string;
  kind: "equity" | "fund";
  intervalIndex: number;
  instrument?: string;
  data: ClusterIntervalPoint[];
  lineWidth: number;
}

export interface ClusterIntervalPositionSeries {
  id: string;
  logicalId: string;
  name: string;
  color: string;
  kind: "position";
  intervalIndex: number;
  instrument?: string;
  data: ClusterIntervalPositionPoint[];
  lineWidth: number;
  stack: "positions";
}

export type ClusterMarkerStatus = InstrumentTradeStatus;

export interface ClusterIntervalMarkPoint {
  seriesId: string;
  instrument: string;
  intervalIndex: number;
  coord: ClusterIntervalPoint;
  value: "B" | "S" | "D";
  action: "BUY" | "SELL";
  status: ClusterMarkerStatus;
  muted: boolean;
  price: number;
  exitDelayDays: number | null;
  fundName: string;
  beforeWeight: number | null;
  afterWeight: number | null;
}

export interface ClusterIntervalBoundaryLine {
  intervalIndex: number;
  xAxis: string;
  name: string;
}

export interface ClusterIntervalChartModel {
  intervals: ClusterInterval[];
  series: ClusterIntervalSeries[];
  positionSeries: ClusterIntervalPositionSeries[];
  markPoints: ClusterIntervalMarkPoint[];
  boundaryLines: ClusterIntervalBoundaryLine[];
}

interface ClusterIntervalTooltipData {
  name?: string;
  tradePrice?: number;
  fundName?: string;
  beforeWeight?: number | null;
  afterWeight?: number | null;
}

function formatClusterMarkerTooltip(data?: ClusterIntervalTooltipData): string {
  const formatWeight = (value: number | null | undefined) =>
    value === null || value === undefined ? "—" : `${(value * 100).toFixed(2)}%`;
  const lines = [
    data?.name ?? "",
    `基金名称：${data?.fundName ?? "—"}`,
    `交易前权重：${formatWeight(data?.beforeWeight)}`,
    `交易后权重：${formatWeight(data?.afterWeight)}`,
  ];
  if (data?.tradePrice !== undefined) lines.push(`成交价：${data.tradePrice}`);
  return lines.join("<br/>");
}

const CHART_BAR_LIMIT = 2000;
const EQUITY_COLOR = "#2563eb";
const CASH_COLOR = "#94a3b8";
const FUND_COLORS = [
  "#0f766e",
  "#9333ea",
  "#c2410c",
  "#0284c7",
  "#be123c",
  "#4d7c0f",
  "#7c3aed",
  "#a16207",
];

function canonicalDate(value: unknown): string {
  const text = String(value ?? "").trim();
  const withoutDecimal = /^\d+\.0$/.test(text) ? text.slice(0, -2) : text;
  const digits = withoutDecimal.replace(/\D/g, "");
  return digits.length >= 8 ? digits.slice(0, 8) : "";
}

function finite(value: unknown): number | null {
  if (
    value === null ||
    value === undefined ||
    typeof value === "boolean" ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function previousCalendarDate(date: string): string {
  const year = Number(date.slice(0, 4));
  const month = Number(date.slice(4, 6));
  const day = Number(date.slice(6, 8));
  if (![year, month, day].every(Number.isFinite)) return date;
  const previous = new Date(Date.UTC(year, month - 1, day - 1));
  return [
    previous.getUTCFullYear(),
    String(previous.getUTCMonth() + 1).padStart(2, "0"),
    String(previous.getUTCDate()).padStart(2, "0"),
  ].join("");
}

function uniqueSortedDates(dates: string[]): string[] {
  return Array.from(new Set(dates.filter(Boolean))).sort();
}

function intervalContains(interval: ClusterInterval, date: string): boolean {
  return date >= interval.start && date <= interval.end;
}

function normalizePoints(
  points: Array<{ date: string; value: number }>,
  positiveBase: boolean,
): ClusterIntervalPoint[] {
  const baseIndex = points.findIndex(({ value }) =>
    Number.isFinite(value) && (positiveBase ? value > 0 : value !== 0),
  );
  if (baseIndex < 0) return [];
  const base = points[baseIndex].value;
  return points
    .slice(baseIndex)
    .filter(({ value }) => Number.isFinite(value))
    .map(({ date, value }) => [date, value / base]);
}

function logicalColor(logicalId: string): string {
  if (logicalId === "equity") return EQUITY_COLOR;
  let hash = 0;
  for (const character of logicalId) {
    hash = (hash * 31 + character.charCodeAt(0)) >>> 0;
  }
  return FUND_COLORS[hash % FUND_COLORS.length];
}

function logicalFundName(
  instrument: string,
  chart: InstrumentChartResponse,
): string {
  return chart.name ? `${chart.name} (${instrument})` : instrument;
}

function periodBounds(period: BacktestPeriod | undefined): {
  start: string;
  end: string;
} {
  return {
    start: canonicalDate(
      period?.evaluation_start_date ??
        period?.decision_start_date ??
        period?.data_start ??
        "",
    ),
    end: canonicalDate(
      period?.evaluation_end_date ??
        period?.evaluation_start_date ??
        period?.decision_start_date ??
        "",
    ),
  };
}

function signalDate(signal: InstrumentSignal): string {
  return canonicalDate(signal.date ?? signal.week_ending ?? "");
}

function signalTarget(signal: InstrumentSignal): number | null {
  return signal.weight !== undefined
    ? finite(signal.weight)
    : finite(signal.target_weight);
}

function tradeWeightDetails(
  trade: InstrumentTrade,
  chart: InstrumentChartResponse,
): { beforeWeight: number | null; afterWeight: number | null } {
  const tradeDate = canonicalDate(trade.trade_date);
  const previousTrade = chart.trades
    .map((candidate, index) => ({
      candidate,
      index,
      date: canonicalDate(candidate.trade_date),
      postHolding: finite(candidate.post_holding),
    }))
    .filter((item) =>
      item.date && item.date < tradeDate && item.postHolding !== null &&
      item.candidate.status !== "REJECTED",
    )
    .sort(
      (left, right) =>
        left.date.localeCompare(right.date) || left.index - right.index,
    )
    .pop();
  const previousPosition = chart.positions
    .map((position, index) => ({
      position,
      index,
      date: canonicalDate(position.trade_date),
      actualWeight: finite(position.actual_weight),
    }))
    .filter((item) => item.date && item.date < tradeDate && item.actualWeight !== null)
    .sort(
      (left, right) =>
        left.date.localeCompare(right.date) || left.index - right.index,
    )
    .pop();
  const beforeWeight = previousTrade?.postHolding === 0
    ? 0
    : previousPosition?.actualWeight ?? null;
  return {
    beforeWeight,
    afterWeight: finite(trade.target_weight),
  };
}

function mutedTradeIndices(
  trades: Array<{ index: number; trade: InstrumentTrade; date: string }>,
  signals: InstrumentSignal[],
): Set<number> {
  const signalTargets = signals
    .map((signal, index) => ({
      date: signalDate(signal),
      index,
      targetWeight: signalTarget(signal),
    }))
    .filter((signal) => signal.date)
    .sort(
      (left, right) =>
        left.date.localeCompare(right.date) || left.index - right.index,
    );
  const latestSignalBefore = (date: string, finiteOnly: boolean) => {
    for (let index = signalTargets.length - 1; index >= 0; index -= 1) {
      const signal = signalTargets[index];
      if (signal.date < date && (!finiteOnly || signal.targetWeight !== null)) {
        return signal;
      }
    }
    return null;
  };

  const orderedTrades = [...trades].sort(
    (left, right) =>
      left.date.localeCompare(right.date) || left.index - right.index,
  );
  const muted = new Set<number>();
  let seenTrade = false;
  let processedSignalDate: string | undefined;
  let processedTargetWeight: number | null = null;
  let previousHolding: number | null = null;
  for (const { index, trade, date } of orderedTrades) {
    const targetWeight = finite(trade.target_weight);
    const currentSignalDate =
      canonicalDate(trade.signal_date ?? trade.signal_week ?? "") ||
      latestSignalBefore(date, false)?.date;
    if (currentSignalDate !== processedSignalDate) {
      processedSignalDate = currentSignalDate;
      processedTargetWeight = currentSignalDate
        ? latestSignalBefore(currentSignalDate, true)?.targetWeight ?? null
        : null;
    }
    if (
      seenTrade &&
      targetWeight !== null &&
      processedTargetWeight !== null &&
      targetWeight === processedTargetWeight &&
      !(trade.action === "BUY" && previousHolding === 0)
    ) {
      muted.add(index);
    }
    if (targetWeight !== null) processedTargetWeight = targetWeight;
    const postHolding = finite(trade.post_holding);
    if (trade.status !== "REJECTED" && postHolding !== null) {
      previousHolding = postHolding;
    }
    seenTrade = true;
  }
  return muted;
}

function buildIntervals(
  input: ClusterIntervalChartInput,
  equityDates: string[],
  chartDates: string[],
): ClusterInterval[] {
  const bounds = periodBounds(input.period);
  const hasExplicitBounds = Boolean(bounds.start || bounds.end);
  const sourceDates = hasExplicitBounds
    ? [...equityDates, ...chartDates]
    : equityDates.length > 0
      ? equityDates
      : chartDates;
  const dataDates = uniqueSortedDates(sourceDates).filter(
    (date) =>
      (!bounds.start || date >= bounds.start) &&
      (!bounds.end || date <= bounds.end),
  );
  if (dataDates.length === 0) return [];
  const start = dataDates[0];
  const end = dataDates[dataDates.length - 1];
  const allReclusterDates = uniqueSortedDates(
    (input.candidatePool?.reclusters ?? [])
      .map((recluster) => canonicalDate(recluster.week))
      .filter(Boolean),
  );
  const precedingReclusters = allReclusterDates.filter(
    (date) => date <= start,
  );
  const precedingRecluster =
    precedingReclusters[precedingReclusters.length - 1] ?? null;
  const visibleReclusters = allReclusterDates.filter(
    (date) => date > start && date <= end,
  );
  const intervalStarts = [
    { start, reclusterDate: precedingRecluster },
    ...visibleReclusters.map((date) => ({ start: date, reclusterDate: date })),
  ];
  return intervalStarts.map((interval, index) => ({
    index,
    start: interval.start,
    end: intervalStarts[index + 1]
      ? previousCalendarDate(intervalStarts[index + 1].start)
      : end,
    reclusterDate: interval.reclusterDate,
  }));
}

function representativeCodesForInterval(
  candidatePool: CandidatePoolResponse | null,
  interval: ClusterInterval,
): Set<string> {
  if (!candidatePool || !interval.reclusterDate) return new Set();
  const recluster = candidatePool.reclusters.find(
    (candidate) => canonicalDate(candidate.week) === interval.reclusterDate,
  );
  return new Set(
    (recluster?.representatives ?? [])
      .map((representative) => String(representative.selected_code ?? "").trim())
      .filter(Boolean),
  );
}

function completedSellDates(chart: InstrumentChartResponse): string[] {
  return chart.trades
    .filter((trade) => {
      if (trade.action !== "SELL" || instrumentTradeStatus(trade) === "REJECTED") {
        return false;
      }
      const postHolding = finite(trade.post_holding);
      return postHolding === 0;
    })
    .map((trade) => canonicalDate(trade.trade_date))
    .filter(Boolean)
    .sort();
}

function actualWeightSeries(
  chart: InstrumentChartResponse,
  dates: string[],
): ClusterIntervalPositionPoint[] {
  type ActualWeightEvent = {
    date: string;
    index: number;
    priority: number;
    weight: number | null;
  };
  const events: ActualWeightEvent[] = [];
  chart.trades.forEach((trade, index) => {
    const date = canonicalDate(trade.trade_date);
    if (!date || instrumentTradeStatus(trade) === "REJECTED") return;
    const postHolding = finite(trade.post_holding);
    if (trade.action === "SELL" && postHolding === 0) {
      events.push({ date, index, priority: 0, weight: 0 });
    } else if (trade.action === "BUY" && postHolding !== null && postHolding > 0) {
      events.push({ date, index, priority: 0, weight: null });
    }
  });
  chart.positions.forEach((position, index) => {
    const date = canonicalDate(position.trade_date);
    if (date) {
      events.push({ date, index, priority: 1, weight: finite(position.actual_weight) });
    }
  });
  events.sort(
    (left, right) =>
      left.date.localeCompare(right.date) ||
      left.priority - right.priority ||
      left.index - right.index,
  );

  let eventIndex = 0;
  let currentWeight: number | null = null;
  return dates.map((date) => {
    while (eventIndex < events.length && events[eventIndex].date <= date) {
      currentWeight = events[eventIndex].weight;
      eventIndex += 1;
    }
    return [date, currentWeight];
  });
}

export function buildClusterIntervalChartModel(
  input: ClusterIntervalChartInput,
): ClusterIntervalChartModel {
  const equityDates = uniqueSortedDates(
    input.equity?.dates.map(canonicalDate) ?? [],
  );
  const chartDates = uniqueSortedDates(
    Object.values(input.charts).flatMap((chart) =>
      [
        ...chart.ohlcv.map((bar) => canonicalDate(bar.trade_date)),
        ...chart.positions.map((position) => canonicalDate(position.trade_date)),
      ],
    ),
  );
  const intervals = buildIntervals(input, equityDates, chartDates);
  const selectedInterval =
    input.selectedIntervalIndex === undefined
      ? null
      : intervals.find((interval) => interval.index === input.selectedIntervalIndex) ?? null;
  const visibleIntervals =
    input.selectedIntervalIndex === undefined
      ? intervals
      : selectedInterval
        ? [selectedInterval]
        : [];
  const selectedRepresentativeCodes = selectedInterval
    ? representativeCodesForInterval(input.candidatePool, selectedInterval)
    : null;
  const series: ClusterIntervalSeries[] = [];
  const positionSeries: ClusterIntervalPositionSeries[] = [];
  const markPoints: ClusterIntervalMarkPoint[] = [];
  const mutedByInstrument = new Map<string, Set<number>>();

  for (const [instrument, chart] of Object.entries(input.charts)) {
    const fullTradeRecords = chart.trades.flatMap((trade, index) => {
      const date = canonicalDate(trade.trade_date);
      return date ? [{ index, trade, date }] : [];
    });
    mutedByInstrument.set(
      instrument,
      mutedTradeIndices(fullTradeRecords, chart.signals),
    );
  }

  for (const interval of visibleIntervals) {
    if (input.equity) {
      const equityPoints = input.equity.dates.flatMap((rawDate, index) => {
        const date = canonicalDate(rawDate);
        const value = finite(input.equity?.series.strategy?.[index]);
        return date && intervalContains(interval, date) && value !== null
          ? [{ date, value }]
          : [];
      });
      const normalized = normalizePoints(equityPoints, false);
      if (normalized.length > 0) {
        series.push({
          id: `equity:${interval.index}`,
          logicalId: "equity",
          name: "组合收益",
          color: logicalColor("equity"),
          kind: "equity",
          intervalIndex: interval.index,
          data: normalized,
          lineWidth: 2.4,
        });
      }
    }

    for (const [instrument, chart] of Object.entries(input.charts)) {
      if (
        selectedRepresentativeCodes &&
        !selectedRepresentativeCodes.has(instrument)
      ) {
        continue;
      }
      const closesByDate = new Map<string, number>();
      for (const bar of chart.ohlcv) {
        const date = canonicalDate(bar.trade_date);
        const close = finite(bar.close);
        if (date && intervalContains(interval, date) && close !== null && close > 0) {
          closesByDate.set(date, close);
        }
      }
      const closePoints = Array.from(closesByDate.entries())
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([date, value]) => ({ date, value }));
      const normalized = normalizePoints(closePoints, true);
      if (normalized.length === 0) continue;

      const seriesId = `fund:${instrument}:${interval.index}`;
      const logicalId = `fund:${instrument}`;
      series.push({
        id: seriesId,
        logicalId,
        name: logicalFundName(instrument, chart),
        color: logicalColor(logicalId),
        kind: "fund",
        intervalIndex: interval.index,
        instrument,
        data: normalized,
        lineWidth: 1.1,
      });

      const normalizedByDate = new Map(normalized);
      const tradeRecords = chart.trades.flatMap((trade, index) => {
        const date = canonicalDate(trade.trade_date);
        return date && intervalContains(interval, date) && normalizedByDate.has(date)
          ? [{ index, trade, date }]
          : [];
      });
      const muted = mutedByInstrument.get(instrument) ?? new Set<number>();
      for (const { index, trade, date } of tradeRecords) {
        const closeValue = normalizedByDate.get(date);
        if (closeValue === undefined) continue;
        const rawPrice = finite(trade.price);
        const exitDelayDays = instrumentTradeExitDelayDays(trade);
        const { beforeWeight, afterWeight } = tradeWeightDetails(trade, chart);
        markPoints.push({
          seriesId,
          instrument,
          intervalIndex: interval.index,
          coord: [date, closeValue],
          value: exitDelayDays !== null
            ? "D"
            : trade.action === "BUY" ? "B" : "S",
          action: trade.action,
          status: instrumentTradeStatus(trade),
          muted: muted.has(index),
          price: rawPrice !== null && rawPrice > 0 ? rawPrice : closePoints[0].value * closeValue,
          exitDelayDays,
          fundName: logicalFundName(instrument, chart),
          beforeWeight,
          afterWeight,
        });
      }
    }

    const representativeCodes = selectedRepresentativeCodes ??
      representativeCodesForInterval(input.candidatePool, interval);
    if (representativeCodes.size === 0) continue;
    const intervalDates = uniqueSortedDates([...equityDates, ...chartDates]).filter(
      (date) => intervalContains(interval, date),
    );
    const intervalPositionSeries: ClusterIntervalPositionSeries[] = [];
    for (const instrument of representativeCodes) {
      const chart = input.charts[instrument];
      const fundSeries = series.find(
        (entry) =>
          entry.kind === "fund" &&
          entry.instrument === instrument &&
          entry.intervalIndex === interval.index,
      );
      if (!chart || !fundSeries) continue;
      const data = actualWeightSeries(chart, intervalDates);
      const hasReliableData = data.some(([, value]) => value !== null);
      const hasCompletedSell = completedSellDates(chart).some((date) =>
        intervalContains(interval, date),
      );
      if (!hasReliableData && !hasCompletedSell) continue;
      intervalPositionSeries.push({
        id: `position:${instrument}:${interval.index}`,
        logicalId: `fund:${instrument}`,
        name: fundSeries.name,
        color: fundSeries.color,
        kind: "position",
        intervalIndex: interval.index,
        instrument,
        data,
        lineWidth: 1,
        stack: "positions",
      });
    }
    positionSeries.push(...intervalPositionSeries);
    if (intervalPositionSeries.length > 0) {
      const allRepresentativesHaveSeries = intervalPositionSeries.length === representativeCodes.size;
      positionSeries.push({
        id: `position:cash:${interval.index}`,
        logicalId: "cash",
        name: "现金",
        color: CASH_COLOR,
        kind: "position",
        intervalIndex: interval.index,
        data: intervalDates.map((date, dateIndex) => {
          if (!allRepresentativesHaveSeries) return [date, null];
          const weights = intervalPositionSeries.map((entry) => entry.data[dateIndex]?.[1]);
          const numericWeights = weights.filter(
            (weight): weight is number => weight !== null && weight !== undefined,
          );
          if (numericWeights.length !== weights.length) {
            return [date, null];
          }
          return [date, Math.max(0, 1 - numericWeights.reduce((sum, weight) => sum + weight, 0))];
        }),
        lineWidth: 1,
        stack: "positions",
      });
    }
  }

  return {
    intervals,
    series,
    positionSeries,
    markPoints,
    boundaryLines: visibleIntervals.flatMap((interval) =>
      interval.reclusterDate && interval.reclusterDate >= interval.start
        ? [{
            intervalIndex: interval.index,
            xAxis: interval.reclusterDate,
            name: `重聚类 · 区间 ${interval.index + 1}`,
          }]
        : [],
    ),
  };
}

interface Props extends ClusterIntervalChartInput {
  height?: number;
}

export function ClusterIntervalChart({
  equity,
  candidatePool,
  charts,
  period,
  height = 420,
}: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const [selectedIntervalIndex, setSelectedIntervalIndex] = useState<number>();
  const baseModel = useMemo(
    () => buildClusterIntervalChartModel({ equity, candidatePool, charts, period }),
    [equity, candidatePool, charts, period],
  );
  const latestIntervalIndex = baseModel.intervals[baseModel.intervals.length - 1]?.index;
  const activeIntervalIndex =
    selectedIntervalIndex !== undefined &&
    baseModel.intervals.some((interval) => interval.index === selectedIntervalIndex)
      ? selectedIntervalIndex
      : latestIntervalIndex;
  const model = useMemo(
    () => buildClusterIntervalChartModel({
      equity,
      candidatePool,
      charts,
      period,
      selectedIntervalIndex: activeIntervalIndex,
    }),
    [activeIntervalIndex, equity, candidatePool, charts, period],
  );
  const selectedInterval = model.intervals.find(
    (interval) => interval.index === activeIntervalIndex,
  );
  const selectedRepresentativeCodes = selectedInterval
    ? representativeCodesForInterval(candidatePool, selectedInterval)
    : new Set<string>();
  const hasNoRepresentatives = Boolean(selectedInterval) && selectedRepresentativeCodes.size === 0;
  const possiblyTruncated = Object.values(charts).some(
    (chart) => chart.ohlcv.length >= CHART_BAR_LIMIT,
  );

  useEffect(() => {
    if (!ref.current || model.series.length === 0) return;
    const theme = getChartTheme();
    const chart = echarts.init(ref.current);
    const dates = uniqueSortedDates(
      [...model.series, ...model.positionSeries].flatMap((entry) =>
        entry.data.map(([date]) => date),
      ),
    );
    const boundaryLines = model.boundaryLines.map((line) => ({
      ...line,
      xAxis: dates.find((date) => date >= line.xAxis) ?? dates[dates.length - 1],
    }));
    const firstSeriesId = model.series[0]?.id;
    const firstEquitySeriesId =
      model.series.find((entry) => entry.kind === "equity")?.id ?? firstSeriesId;
    const intervalAreas = model.intervals.flatMap((interval) => {
      const intervalDates = dates.filter((date) => intervalContains(interval, date));
      if (intervalDates.length === 0) return [];
      return [[
        { xAxis: intervalDates[0] },
        { xAxis: intervalDates[intervalDates.length - 1] },
      ]];
    });
    const priceSeries = model.series.map((entry) => {
      const marks = model.markPoints
        .filter((mark) => mark.seriesId === entry.id)
        .map((mark) => {
          const visual = getTradeMarkerVisual({
            side: mark.action,
            status: mark.status,
            muted: mark.muted,
            exit_delay_days: mark.exitDelayDays ?? undefined,
          }, theme);
          const state = mark.exitDelayDays !== null
            ? `延迟 ${mark.exitDelayDays} 天`
            : mark.status;
          return {
            coord: mark.coord,
            value: mark.value,
            name: `${mark.action} · ${state}`,
            tradePrice: mark.price,
            fundName: mark.fundName,
            beforeWeight: mark.beforeWeight,
            afterWeight: mark.afterWeight,
            itemStyle: { color: visual.color },
            label: { color: "#fff", fontSize: 10, fontWeight: "bold" as const },
          };
        });
      const isAreaSeries = entry.id === firstEquitySeriesId;
      return {
        id: entry.id,
        logicalId: entry.logicalId,
        name: entry.name,
        type: "line" as const,
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: entry.data,
        symbol: "none",
        showSymbol: false,
        connectNulls: false,
        lineStyle: { width: entry.lineWidth, color: entry.color },
        itemStyle: { color: entry.color },
        markPoint: marks.length > 0
          ? {
              data: marks,
              symbol: "circle",
              symbolSize: 24,
              tooltip: {
                trigger: "item" as const,
                formatter: (params: { data?: ClusterIntervalTooltipData }) =>
                  formatClusterMarkerTooltip(params.data),
              },
            }
          : undefined,
        markLine: entry.id === firstSeriesId
          ? {
              symbol: ["none", "none"],
              label: { color: theme.textColor, fontSize: 10 },
              lineStyle: { color: theme.axisColor, type: "dashed" as const, opacity: 0.65 },
              data: boundaryLines.map((line) => ({ xAxis: line.xAxis, name: line.name })),
            }
          : undefined,
        markArea: isAreaSeries
          ? {
              silent: true,
              itemStyle: { color: theme.gridColor, opacity: 0.08 },
              data: intervalAreas,
            }
          : undefined,
      };
    });
    const positionChartSeries = model.positionSeries.map((entry) => ({
      id: entry.id,
      logicalId: entry.logicalId,
      name: entry.name,
      type: "line" as const,
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: entry.data,
      stack: entry.stack,
      step: "end" as const,
      symbol: "none",
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: entry.lineWidth, color: entry.color },
      itemStyle: { color: entry.color },
      areaStyle: { color: entry.color, opacity: 0.55 },
      markLine: entry.id === (
        model.positionSeries.find((candidate) => candidate.logicalId === "cash")?.id ??
        model.positionSeries[0]?.id
      ) && boundaryLines.length > 0
        ? {
            xAxisIndex: 1,
            yAxisIndex: 1,
            symbol: ["none", "none"],
            label: { color: theme.textColor, fontSize: 10 },
            lineStyle: { color: theme.axisColor, type: "dashed" as const, opacity: 0.65 },
            data: boundaryLines.map((line) => ({ xAxis: line.xAxis, name: line.name })),
          }
        : undefined,
    }));
    const optionSeries = [...priceSeries, ...positionChartSeries];
    const seriesLogicalIds = new Map(
      optionSeries.map((entry) => [entry.id, entry.logicalId]),
    );
    const positionSeriesById = new Map(
      model.positionSeries.map((entry) => [entry.id, entry]),
    );
    const baseLineStyles = new Map<string, { width: number; opacity?: number }>(
      optionSeries.map((entry) => [
        entry.id,
        {
          width: entry.lineStyle.width ?? 1,
          opacity: (entry.lineStyle as { opacity?: number }).opacity,
        },
      ]),
    );

    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontSize: 11 },
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        formatter: (params: any) => {
          if (!Array.isArray(params)) {
            return params?.data?.fundName
              ? formatClusterMarkerTooltip(params.data)
              : "";
          }
           if (params.length === 0) return "";
           const date = String(params[0].axisValue ?? "");
           const interval = model.intervals.find((candidate) => intervalContains(candidate, date));
           let html = `<b>${date}</b>`;
           if (interval) html += `<br/>区间：${interval.index + 1}`;
           const hasPositionParam = params.some((param: any) =>
             positionSeriesById.has(String(param?.seriesId)),
           );
           if (hasPositionParam) {
             let total = 0;
             let allReliable = true;
             for (const entry of model.positionSeries) {
               const value = entry.data.find(([entryDate]) => entryDate === date)?.[1] ?? null;
               if (value === null || value === undefined || !Number.isFinite(value)) {
                 html += `<br/>${entry.name}：缺失`;
                 allReliable = false;
                 continue;
               }
               total += value;
               html += `<br/>${entry.name}：${(value * 100).toFixed(2)}%`;
             }
             if (allReliable) html += `<br/>合计：${(total * 100).toFixed(2)}%`;
             return html;
           }
           for (const param of params) {
            if (param.value !== null && param.value !== undefined) {
              const value = Array.isArray(param.value) ? param.value[1] : param.value;
              if (Number.isFinite(Number(value))) {
                html += `<br/>${param.marker ?? ""} ${param.seriesName}：${Number(value).toFixed(3)}`;
              }
            }
          }
          return html;
        },
      },
      toolbox: {
        feature: { saveAsImage: { title: "保存" }, restore: { title: "重置" } },
        right: 8,
        top: 0,
        iconStyle: { borderColor: theme.textColor },
      },
      legend: {
        data: Array.from(
          new Map(
            [...model.series, ...model.positionSeries].map((entry) => [entry.logicalId, entry.name]),
          ).values(),
        ),
        textStyle: { color: theme.textColor, fontSize: 10 },
        top: 4,
        left: 8,
        right: 80,
        type: "scroll",
      },
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      grid: [
        { left: 12, right: 12, top: 42, height: "52%", containLabel: true },
        { left: 12, right: 12, top: "63%", height: "27%", containLabel: true },
      ],
      xAxis: [
        {
          type: "category",
          data: dates,
          boundaryGap: false,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { show: false },
        },
        {
          type: "category",
          gridIndex: 1,
          data: dates,
          boundaryGap: false,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor, fontSize: 10 },
        },
      ],
      yAxis: [
        {
          type: "value",
          scale: true,
          splitLine: { lineStyle: { color: theme.gridColor } },
          axisLabel: {
            color: theme.textColor,
            fontSize: 10,
            formatter: (value: number) => value.toFixed(2),
          },
        },
        {
          type: "value",
          gridIndex: 1,
          min: 0,
          max: 1,
          interval: 0.25,
          splitLine: { lineStyle: { color: theme.gridColor } },
          axisLabel: {
            color: theme.textColor,
            fontSize: 10,
            formatter: (value: number) => `${(value * 100).toFixed(0)}%`,
          },
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], bottom: 4, height: 16 },
      ],
      series: optionSeries,
    });

    const applyHighlight = (activeLogicalId: string | null) => {
      chart.setOption({
        series: optionSeries.map((entry) => {
          const baseLineStyle = baseLineStyles.get(entry.id) ?? { width: 1, opacity: 1 };
          const isEquity = entry.logicalId === "equity";
          const isPosition = "stack" in entry;
          const isActive = activeLogicalId !== null && entry.logicalId === activeLogicalId;
          const visible = activeLogicalId === null || isEquity || isActive;
          return {
            id: entry.id,
            lineStyle: {
              opacity: activeLogicalId === null
                ? baseLineStyle.opacity
                : visible
                  ? isEquity
                    ? baseLineStyle.opacity
                    : baseLineStyle.opacity ?? 1
                  : 0.18,
              width: isActive ? baseLineStyle.width + 0.5 : baseLineStyle.width,
            },
            itemStyle: {
              opacity: activeLogicalId === null
                ? undefined
                : visible
                  ? isEquity
                    ? baseLineStyle.opacity
                    : baseLineStyle.opacity ?? 1
                  : 0.18,
            },
            ...(isPosition
              ? { areaStyle: { opacity: activeLogicalId === null || isEquity ? 0.55 : isActive ? 0.85 : visible ? 0.55 : 0.12 } }
              : {}),
          };
        }),
      });
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handleMouseOver = (params: any) => {
      const logicalId = seriesLogicalIds.get(String(params?.seriesId));
      if (logicalId?.startsWith("fund:") || logicalId === "cash") {
        applyHighlight(logicalId);
      }
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handleMouseOut = () => applyHighlight(null);
    chart.on("mouseover", handleMouseOver);
    chart.on("mouseout", handleMouseOut);

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.off("mouseover", handleMouseOver);
      chart.off("mouseout", handleMouseOut);
      chart.dispose();
    };
  }, [dark, model]);

  return (
    <div className="space-y-2">
      {possiblyTruncated && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          行情数据可能被截断
        </div>
      )}
      {model.intervals.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="cluster-interval-select" className="text-sm font-medium">
            选择聚类区间
          </label>
          <select
            id="cluster-interval-select"
            aria-label="选择聚类区间"
            className="rounded border bg-background px-2 py-1 text-sm"
            value={activeIntervalIndex === undefined ? "" : String(activeIntervalIndex)}
            onChange={(event) => setSelectedIntervalIndex(Number(event.target.value))}
          >
            {model.intervals.map((interval) => (
              <option key={interval.index} value={interval.index}>
                {`重聚类 ${interval.reclusterDate ? formatDisplayDate(interval.reclusterDate) : "未知"} · 区间 ${formatDisplayDate(interval.start)} 至 ${formatDisplayDate(interval.end)}`}
              </option>
            ))}
          </select>
        </div>
      )}
      {model.intervals.length === 0 ? (
        <div className="rounded border bg-muted/20 px-3 py-6 text-center text-sm text-muted-foreground">
          当前没有可用的聚类区间。
        </div>
      ) : (
        <>
          {hasNoRepresentatives && (
            <div className="rounded border bg-muted/20 px-3 py-2 text-sm text-muted-foreground">
              当前区间没有可用的代表基金，以下仅展示组合收益。
            </div>
          )}
          {model.series.length === 0 ? (
            <div className="rounded border bg-muted/20 px-3 py-6 text-center text-sm text-muted-foreground">
              当前没有可用的聚类区间收益或基金行情数据。
            </div>
          ) : (
            <div ref={ref} style={{ height }} />
          )}
        </>
      )}
    </div>
  );
}

function formatDisplayDate(date: string): string {
  return date.length === 8
    ? `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`
    : date;
}
