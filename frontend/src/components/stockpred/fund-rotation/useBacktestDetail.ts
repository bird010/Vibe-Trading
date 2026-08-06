/** Isolated child-run detail state with stale-request protection. */

import { create } from "zustand";
import {
  fetchBacktestDetail,
  fetchBacktestEquity,
  fetchInstrumentChart,
} from "./api";
import type {
  BacktestDetailResponse,
  BacktestDetailTab,
  ComparisonEquityData,
  InstrumentChartResponse,
} from "./types";

let detailAbortController: AbortController | null = null;
let chartAbortController: AbortController | null = null;
let detailRequestId = 0;
let chartRequestId = 0;

const CHART_BAR_LIMIT = 2000;

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function defaultInstrument(detail: BacktestDetailResponse): string | null {
  return (
    detail.instruments.find((instrument) => instrument.has_trade)?.ts_code ??
    detail.instruments.find((instrument) => instrument.has_signal)?.ts_code ??
    detail.instruments[0]?.ts_code ??
    null
  );
}

export interface BacktestDetailState {
  selectedVariantKey: string | null;
  selectedRunId: string | null;
  detail: BacktestDetailResponse | null;
  equity: ComparisonEquityData | null;
  activeTab: BacktestDetailTab;
  selectedInstrument: string | null;
  chart: InstrumentChartResponse | null;
  loading: boolean;
  chartLoading: boolean;
  error: string | null;
  chartError: string | null;
  openRun: (variantKey: string, runId: string) => Promise<void>;
  closeRun: () => void;
  selectTab: (tab: BacktestDetailTab) => void;
  selectInstrument: (tsCode: string) => Promise<void>;
}

export const useBacktestDetail = create<BacktestDetailState>((set, get) => ({
  selectedVariantKey: null,
  selectedRunId: null,
  detail: null,
  equity: null,
  activeTab: "overview",
  selectedInstrument: null,
  chart: null,
  loading: false,
  chartLoading: false,
  error: null,
  chartError: null,

  openRun: async (variantKey, runId) => {
    const requestId = ++detailRequestId;
    detailAbortController?.abort();
    chartAbortController?.abort();
    detailAbortController = new AbortController();
    chartRequestId += 1;

    set({
      selectedVariantKey: variantKey,
      selectedRunId: runId,
      detail: null,
      equity: null,
      activeTab: "overview",
      selectedInstrument: null,
      chart: null,
      loading: true,
      chartLoading: false,
      error: null,
      chartError: null,
    });

    try {
      const [detail, equity] = await Promise.all([
        fetchBacktestDetail(runId, detailAbortController.signal),
        fetchBacktestEquity(runId, detailAbortController.signal),
      ]);
      if (requestId !== detailRequestId || get().selectedRunId !== runId) return;
      set({
        detail,
        equity,
        selectedInstrument: defaultInstrument(detail),
        loading: false,
      });
    } catch (error) {
      if (requestId !== detailRequestId || isAbortError(error)) return;
      set({
        loading: false,
        error: error instanceof Error ? error.message : "回测详情加载失败",
      });
    }
  },

  closeRun: () => {
    detailRequestId += 1;
    chartRequestId += 1;
    detailAbortController?.abort();
    chartAbortController?.abort();
    detailAbortController = null;
    chartAbortController = null;
    set({
      selectedVariantKey: null,
      selectedRunId: null,
      detail: null,
      equity: null,
      activeTab: "overview",
      selectedInstrument: null,
      chart: null,
      loading: false,
      chartLoading: false,
      error: null,
      chartError: null,
    });
  },

  selectTab: (tab) => set({ activeTab: tab }),

  selectInstrument: async (tsCode) => {
    const runId = get().selectedRunId;
    if (!runId) return;
    const requestId = ++chartRequestId;
    chartAbortController?.abort();
    chartAbortController = new AbortController();
    set({
      selectedInstrument: tsCode,
      chart: null,
      chartLoading: true,
      chartError: null,
    });
    try {
      const chart = await fetchInstrumentChart(
        runId,
        tsCode,
        CHART_BAR_LIMIT,
        chartAbortController.signal,
      );
      if (
        requestId !== chartRequestId ||
        get().selectedRunId !== runId ||
        get().selectedInstrument !== tsCode
      ) {
        return;
      }
      set({ chart, chartLoading: false });
    } catch (error) {
      if (requestId !== chartRequestId || isAbortError(error)) return;
      set({
        chartLoading: false,
        chartError: error instanceof Error ? error.message : "K 线证据加载失败",
      });
    }
  },
}));
