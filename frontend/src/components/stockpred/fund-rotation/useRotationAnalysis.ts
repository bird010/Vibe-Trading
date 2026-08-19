import { create } from "zustand";
import {
  fetchHoldingsTimeline,
  fetchRebalanceDecision,
  fetchRebalanceIndex,
} from "./api";
import type {
  HoldingsTimelineResponse,
  RebalanceDecisionResponse,
  RebalanceIndexResponse,
} from "./types";
import { readFundRotationUrl } from "./deepLinks";

let overviewAbortController: AbortController | null = null;
let decisionAbortController: AbortController | null = null;
let overviewRequestId = 0;
let decisionRequestId = 0;

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function initialRebalanceSignalDate(
  items: RebalanceIndexResponse["items"],
): string | null {
  return [...items]
    .reverse()
    .find((item) => (item.actual_changed_positions ?? item.changed_positions) > 0 || (item.execution_turnover ?? 0) > 0)
    ?.signal_date ?? null;
}

export interface RotationAnalysisState {
  runId: string | null;
  timeline: HoldingsTimelineResponse | null;
  rebalanceIndex: RebalanceIndexResponse | null;
  rebalanceDetails: Record<string, RebalanceDecisionResponse>;
  selectedSignalDate: string | null;
  timelineWindow: { start: string; end: string } | null;
  candidateView: "changed" | "top" | "all";
  loading: boolean;
  error: string | null;
  decisionLoading: boolean;
  decisionErrors: Record<string, string>;
  openRun: (runId: string) => Promise<void>;
  selectSignalDate: (signalDate: string) => Promise<void>;
  setTimelineWindow: (window: { start: string; end: string } | null) => void;
  setCandidateView: (view: "changed" | "top" | "all") => void;
  reset: () => void;
}

const initialState = {
  runId: null,
  timeline: null,
  rebalanceIndex: null,
  rebalanceDetails: {},
  selectedSignalDate: null,
  timelineWindow: null,
  candidateView: "changed" as const,
  loading: false,
  error: null,
  decisionLoading: false,
  decisionErrors: {},
};

export const useRotationAnalysis = create<RotationAnalysisState>((set, get) => ({
  ...initialState,

  openRun: async (runId) => {
    const urlState = readFundRotationUrl();
    const restoredSignalDate = urlState.runId === runId && urlState.tab === "rotation_analysis"
      ? urlState.signalDate
      : null;
    const requestId = ++overviewRequestId;
    overviewAbortController?.abort();
    decisionAbortController?.abort();
    overviewAbortController = new AbortController();
    decisionRequestId += 1;
    set({
      ...initialState,
      runId,
      selectedSignalDate: restoredSignalDate,
      loading: true,
    });
    const signal = overviewAbortController.signal;
    const [timelineResult, indexResult] = await Promise.allSettled([
      fetchHoldingsTimeline(runId, signal),
      fetchRebalanceIndex(runId, signal),
    ]);
    if (requestId !== overviewRequestId || get().runId !== runId) return;
    const next: Partial<RotationAnalysisState> = { loading: false };
    const errors: string[] = [];
    if (timelineResult.status === "fulfilled") {
      next.timeline = timelineResult.value;
      next.timelineWindow = {
        start: timelineResult.value.start_date ?? "",
        end: timelineResult.value.end_date ?? "",
      };
    } else if (!isAbortError(timelineResult.reason)) {
      errors.push(errorMessage(timelineResult.reason, "持仓时间线加载失败"));
    }
    if (indexResult.status === "fulfilled") {
      next.rebalanceIndex = indexResult.value;
    } else if (!isAbortError(indexResult.reason)) {
      errors.push(errorMessage(indexResult.reason, "调仓索引加载失败"));
    }
    next.error = errors.length > 0 ? errors.join("；") : null;
    set(next);
    const defaultSignalDate = !restoredSignalDate && indexResult.status === "fulfilled"
      ? initialRebalanceSignalDate(indexResult.value.items)
      : null;
    const signalDateToLoad = restoredSignalDate ?? defaultSignalDate;
    if (signalDateToLoad) void get().selectSignalDate(signalDateToLoad);
  },

  selectSignalDate: async (signalDate) => {
    const runId = get().runId;
    if (!runId) return;
    set({ selectedSignalDate: signalDate });
    if (get().rebalanceDetails[signalDate]) return;
    const requestId = ++decisionRequestId;
    decisionAbortController?.abort();
    decisionAbortController = new AbortController();
    set({ decisionLoading: true });
    try {
      const detail = await fetchRebalanceDecision(
        runId,
        signalDate,
        decisionAbortController.signal,
      );
      if (
        requestId !== decisionRequestId ||
        get().runId !== runId ||
        get().selectedSignalDate !== signalDate
      ) return;
      set((state) => ({
        rebalanceDetails: { ...state.rebalanceDetails, [signalDate]: detail },
        decisionLoading: false,
      }));
    } catch (error) {
      if (requestId !== decisionRequestId || isAbortError(error)) return;
      set((state) => ({
        decisionLoading: false,
        decisionErrors: {
          ...state.decisionErrors,
          [signalDate]: errorMessage(error, "调仓决策加载失败"),
        },
      }));
    }
  },

  setTimelineWindow: (timelineWindow) => set({ timelineWindow }),
  setCandidateView: (candidateView) => set({ candidateView }),

  reset: () => {
    overviewRequestId += 1;
    decisionRequestId += 1;
    overviewAbortController?.abort();
    decisionAbortController?.abort();
    overviewAbortController = null;
    decisionAbortController = null;
    set(initialState);
  },
}));
