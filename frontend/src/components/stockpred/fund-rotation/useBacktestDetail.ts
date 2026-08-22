/** Isolated child-run detail state with stale-request protection. */

import { create } from "zustand";
import {
  fetchCandidatePool,
  fetchBacktestDetail,
  fetchBacktestEquity,
  fetchInstrumentChart,
} from "./api";
import type {
  BacktestDetailResponse,
  BacktestDetailTab,
  CandidatePoolResponse,
  ComparisonEquityData,
  InstrumentChartResponse,
} from "./types";
import { readFundRotationUrl, syncFundRotationUrl } from "./deepLinks";

let detailAbortController: AbortController | null = null;
let chartAbortController: AbortController | null = null;
let chartsAbortController: AbortController | null = null;
let detailRequestId = 0;
let chartRequestId = 0;
let chartsRequestId = 0;
let candidatePoolAbortController: AbortController | null = null;
let candidatePoolRequestId = 0;

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
  candidatePool: CandidatePoolResponse | null;
  candidatePoolLoading: boolean;
  candidatePoolError: string | null;
  activeTab: BacktestDetailTab;
  selectedInstrument: string | null;
  chart: InstrumentChartResponse | null;
  charts: Record<string, InstrumentChartResponse>;
  loading: boolean;
  chartLoading: boolean;
  error: string | null;
  chartError: string | null;
  chartErrors: Record<string, string>;
  openRun: (variantKey: string, runId: string) => Promise<void>;
  closeRun: () => void;
  selectTab: (tab: BacktestDetailTab) => void;
  selectInstrument: (tsCode: string) => Promise<void>;
  loadCharts: () => Promise<void>;
  loadCandidatePool: () => Promise<void>;
}

export const useBacktestDetail = create<BacktestDetailState>((set, get) => ({
  selectedVariantKey: null,
  selectedRunId: null,
  detail: null,
  equity: null,
  candidatePool: null,
  candidatePoolLoading: false,
  candidatePoolError: null,
  activeTab: "overview",
  selectedInstrument: null,
  chart: null,
  charts: {},
  loading: false,
  chartLoading: false,
  error: null,
  chartError: null,
  chartErrors: {},

  openRun: async (variantKey, runId) => {
    const urlState = readFundRotationUrl();
    const restoreUrlState = urlState.runId === runId ? urlState : null;
    const requestId = ++detailRequestId;
    detailAbortController?.abort();
    chartAbortController?.abort();
    chartsAbortController?.abort();
    candidatePoolAbortController?.abort();
    detailAbortController = new AbortController();
    chartRequestId += 1;
    chartsRequestId += 1;
    candidatePoolRequestId += 1;

    set({
      selectedVariantKey: variantKey,
      selectedRunId: runId,
      detail: null,
      equity: null,
      candidatePool: null,
      candidatePoolLoading: false,
      candidatePoolError: null,
      activeTab: restoreUrlState?.tab ?? "overview",
      selectedInstrument: restoreUrlState?.instrument,
      chart: null,
      charts: {},
      loading: true,
      chartLoading: false,
      error: null,
      chartError: null,
      chartErrors: {},
    });
    syncFundRotationUrl({
      runId,
      tab: restoreUrlState?.tab ?? "overview",
      signalDate: restoreUrlState?.signalDate ?? null,
      instrument: restoreUrlState?.instrument ?? null,
      focusDate: restoreUrlState?.focusDate ?? null,
      strategyScore: restoreUrlState?.strategyScore ?? null,
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
        selectedInstrument: restoreUrlState?.instrument ?? defaultInstrument(detail),
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
    chartsRequestId += 1;
    candidatePoolRequestId += 1;
    detailAbortController?.abort();
    chartAbortController?.abort();
    chartsAbortController?.abort();
    detailAbortController = null;
    chartAbortController = null;
    chartsAbortController = null;
    candidatePoolAbortController?.abort();
    candidatePoolAbortController = null;
    set({
      selectedVariantKey: null,
      selectedRunId: null,
      detail: null,
      equity: null,
      candidatePool: null,
      candidatePoolLoading: false,
      candidatePoolError: null,
      activeTab: "overview",
      selectedInstrument: null,
      chart: null,
      charts: {},
      loading: false,
      chartLoading: false,
      error: null,
      chartError: null,
      chartErrors: {},
    });
  },

  selectTab: (tab) => {
    set({ activeTab: tab });
    const runId = get().selectedRunId;
    if (runId) syncFundRotationUrl({ runId, tab }, "push");
  },

  selectInstrument: async (tsCode) => {
    const runId = get().selectedRunId;
    if (!runId) return;
    const cached = get().charts[tsCode];
    if (cached) {
      set({ selectedInstrument: tsCode, chart: cached, chartLoading: false, chartError: null });
      return;
    }
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
        get().detail?.period.evaluation_start_date,
        get().detail?.period.evaluation_end_date,
      );
      if (
        requestId !== chartRequestId ||
        get().selectedRunId !== runId ||
        get().selectedInstrument !== tsCode
      ) {
        return;
      }
      set((state) => ({ chart, chartLoading: false, charts: { ...state.charts, [tsCode]: chart } }));
    } catch (error) {
      if (requestId !== chartRequestId || isAbortError(error)) return;
      set({
        chartLoading: false,
        chartError: error instanceof Error ? error.message : "K 线证据加载失败",
      });
    }
  },

  loadCharts: async () => {
    const runId = get().selectedRunId;
    const instruments = get().detail?.instruments;
    if (!runId || !instruments || instruments.length === 0) return;
    if (get().chartLoading) return;
    const missingInstruments = instruments.filter(
      (instrument) => !get().charts[instrument.ts_code],
    );
    if (missingInstruments.length === 0) {
      if (Object.keys(get().chartErrors).length > 0) set({ chartErrors: {} });
      return;
    }

    const requestId = ++chartsRequestId;
    chartsAbortController?.abort();
    const abortController = new AbortController();
    chartsAbortController = abortController;
    set({ chartLoading: true, chartErrors: {} });

    const results = await Promise.allSettled(
      missingInstruments.map((instrument) =>
        fetchInstrumentChart(
          runId,
          instrument.ts_code,
          CHART_BAR_LIMIT,
          abortController.signal,
          get().detail?.period.evaluation_start_date,
          get().detail?.period.evaluation_end_date,
        ),
      ),
    );
    if (requestId !== chartsRequestId || get().selectedRunId !== runId) return;

    const charts: Record<string, InstrumentChartResponse> = {};
    const chartErrors: Record<string, string> = {};
    results.forEach((result, index) => {
      const tsCode = missingInstruments[index].ts_code;
      if (result.status === "fulfilled") {
        charts[tsCode] = result.value;
      } else if (!isAbortError(result.reason)) {
        chartErrors[tsCode] =
          result.reason instanceof Error ? result.reason.message : "K 线证据加载失败";
      }
    });
    set((state) => ({
      charts: { ...state.charts, ...charts },
      chartErrors,
      chartLoading: false,
    }));
  },

  loadCandidatePool: async () => {
    const runId = get().selectedRunId;
    if (!runId) return;
    if (get().candidatePoolLoading || get().candidatePool) return;

    const requestId = ++candidatePoolRequestId;
    candidatePoolAbortController?.abort();
    candidatePoolAbortController = new AbortController();
    set({ candidatePoolLoading: true, candidatePoolError: null });

    try {
      const candidatePool = await fetchCandidatePool(
        runId,
        candidatePoolAbortController.signal,
      );
      if (
        requestId !== candidatePoolRequestId ||
        get().selectedRunId !== runId
      ) {
        return;
      }
      set({ candidatePool, candidatePoolLoading: false });
    } catch (error) {
      if (requestId !== candidatePoolRequestId || isAbortError(error)) return;
      set({
        candidatePoolLoading: false,
        candidatePoolError:
          error instanceof Error ? error.message : "基金候选池加载失败",
      });
    }
  },
}));
