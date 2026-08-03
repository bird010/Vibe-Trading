/** Zustand store for the fund-rotation catalog and strategy batches. */

import { create } from "zustand";
import type {
  BatchDetail,
  BatchListItem,
  BatchSubmitResponse,
  ComparisonEquityData,
  EventEnvelope,
  StrategyDetail,
  StrategySummary,
  StrategyBatchRequest,
} from "./types";
import {
  fetchStrategies,
  fetchStrategyDetail,
  submitBatch,
  fetchBatches,
  fetchBatchDetail,
  cancelBatch,
  connectBatchSSE,
  fetchBatchReports,
  fetchBatchComparisonEquity,
  fetchBacktestEquity,
} from "./api";
import type { VariantDraft } from "./StrategyVariantsEditor";

let eventSource: EventSource | null = null;
let connectedBatchId: string | null = null;

const TERMINAL_STAGES = new Set([
  "SUCCEEDED",
  "PARTIAL_SUCCEEDED",
  "FAILED",
  "CANCELED",
  "FAILED_INTERRUPTED",
]);

function mergeComparisonAndBenchmarks(
  comparison: ComparisonEquityData | null,
  childEquity: ComparisonEquityData | null,
): ComparisonEquityData | null {
  if (!comparison) return null;
  if (!childEquity) return comparison;
  if (
    comparison.dates.length !== childEquity.dates.length ||
    comparison.dates.some((date, index) => date !== childEquity.dates[index])
  ) {
    return comparison;
  }

  const benchmarkSeries = Object.fromEntries(
    Object.entries(childEquity.series)
      .filter(([name]) => name !== "strategy")
      .map(([name, values]) => [`benchmark:${name}`, values]),
  );
  return {
    dates: comparison.dates,
    series: {
      ...comparison.series,
      ...benchmarkSeries,
    },
  };
}

export interface FundRotationState {
  catalogVersion: string;
  strategies: StrategySummary[];
  strategyDetails: Map<string, StrategyDetail>;
  catalogLoading: boolean;
  catalogError: string | null;
  batches: BatchListItem[];
  activeBatchId: string | null;
  activeBatch: BatchDetail | null;
  comparison: Awaited<ReturnType<typeof fetchBatchReports>> | null;
  comparisonEquity: ComparisonEquityData | null;
  loading: boolean;
  error: string | null;
  events: EventEnvelope[];
  fetchCatalog: () => Promise<void>;
  fetchBatches: () => Promise<void>;
  submitStrategyBatch: (
    variants: VariantDraft[],
    evaluationStart: string,
    evaluationEnd: string,
    execution: StrategyBatchRequest["execution"],
    idempotencyKey: string,
  ) => Promise<BatchSubmitResponse>;
  selectBatch: (batchId: string) => Promise<void>;
  cancelActiveBatch: () => Promise<boolean>;
  connectBatchSSE: (batchId: string) => void;
  disconnectSSE: () => void;
  reset: () => void;
}

export const useFundRotation = create<FundRotationState>((set, get) => ({
  catalogVersion: "",
  strategies: [],
  strategyDetails: new Map(),
  catalogLoading: false,
  catalogError: null,
  batches: [],
  activeBatchId: null,
  activeBatch: null,
  comparison: null,
  comparisonEquity: null,
  loading: false,
  error: null,
  events: [],

  fetchCatalog: async () => {
    set({ catalogLoading: true, catalogError: null });
    try {
      const list = await fetchStrategies();
      const details = new Map<string, StrategyDetail>();
      const failures: string[] = [];
      await Promise.all(
        list.strategies.map(async (strategy) => {
          try {
            const result = await fetchStrategyDetail(strategy.strategy_id);
            if (result.data) details.set(strategy.strategy_id, result.data);
            else failures.push(`${strategy.strategy_id}: empty detail`);
          } catch (error) {
            failures.push(
              `${strategy.strategy_id}: ${
                error instanceof Error ? error.message : "detail load failed"
              }`,
            );
          }
        }),
      );
      set({
        catalogVersion: list.catalog_version,
        strategies: list.strategies,
        strategyDetails: details,
        catalogLoading: false,
        catalogError:
          failures.length > 0
            ? `以下策略定义加载失败，已禁用：${failures.join("；")}`
            : null,
      });
    } catch (error) {
      set({
        catalogError:
          error instanceof Error ? error.message : "Failed to load catalog",
        catalogLoading: false,
      });
    }
  },

  fetchBatches: async () => {
    try {
      set({ batches: await fetchBatches(), error: null });
    } catch (error) {
      set({
        error:
          error instanceof Error ? error.message : "Failed to load batches",
      });
    }
  },

  submitStrategyBatch: async (
    variants,
    evaluationStart,
    evaluationEnd,
    execution,
    idempotencyKey,
  ) => {
    set({ loading: true, error: null });
    try {
      const request: StrategyBatchRequest = {
        schema_version: "1",
        idempotency_key: idempotencyKey,
        mode: "RESEARCH_ONLY",
        evaluation_start_date: evaluationStart,
        evaluation_end_date: evaluationEnd,
        execution,
        variants: variants.map((variant) => ({
          strategy_id: variant.strategyId,
          label: variant.label || undefined,
          params: variant.params,
        })),
      };
      const result = await submitBatch(request);
      set({ loading: false });
      await get().fetchBatches();
      return result;
    } catch (error) {
      set({
        loading: false,
        error:
          error instanceof Error ? error.message : "Submission failed",
      });
      throw error;
    }
  },

  selectBatch: async (batchId: string) => {
    get().disconnectSSE();
    set({
      activeBatchId: batchId,
      activeBatch: null,
      comparison: null,
      comparisonEquity: null,
      events: [],
      error: null,
    });
    try {
      const detail = await fetchBatchDetail(batchId);
      set({ activeBatch: detail });
      if (TERMINAL_STAGES.has(detail.state.stage)) {
        if (
          detail.state.stage === "SUCCEEDED" ||
          detail.state.stage === "PARTIAL_SUCCEEDED"
        ) {
          try {
            const reports = await fetchBatchReports(batchId);
            let comparisonEquity: ComparisonEquityData | null = null;
            if (reports.comparison_available) {
              const parentEquity = await fetchBatchComparisonEquity(batchId);
              let childEquity: ComparisonEquityData | null = null;
              const referenceRunId = reports.ranking[0]?.run_id;
              if (referenceRunId) {
                try {
                  childEquity = await fetchBacktestEquity(referenceRunId);
                } catch {
                  // Benchmarks are optional display enrichment; parent strategy
                  // comparison remains valid if the child artifact is missing.
                }
              }
              comparisonEquity = mergeComparisonAndBenchmarks(
                parentEquity,
                childEquity,
              );
            }
            set({ comparison: reports, comparisonEquity });
          } catch (error) {
            set({
              error:
                error instanceof Error
                  ? error.message
                  : "Failed to load comparison reports",
            });
          }
        }
      } else {
        get().connectBatchSSE(batchId);
      }
    } catch (error) {
      set({
        error:
          error instanceof Error ? error.message : "Failed to load batch",
      });
    }
  },

  cancelActiveBatch: async () => {
    const batchId = get().activeBatchId;
    if (!batchId) return false;
    try {
      const result = await cancelBatch(batchId);
      return result.cancelled;
    } catch (error) {
      set({
        error:
          error instanceof Error ? error.message : "Failed to cancel batch",
      });
      return false;
    }
  },

  connectBatchSSE: (batchId: string) => {
    if (eventSource && connectedBatchId === batchId) return;
    get().disconnectSSE();
    const existingEvents = get().events;
    const lastSequence =
      existingEvents.length > 0
        ? existingEvents[existingEvents.length - 1].seq
        : null;
    connectedBatchId = batchId;
    eventSource = connectBatchSSE(
      batchId,
      lastSequence,
      (rawEvent) => {
        const event = rawEvent as unknown as EventEnvelope;
        set((state) => {
          if (state.activeBatchId !== batchId) return {};
          const exists = state.events.some((item) => item.seq === event.seq);
          const events = exists ? state.events : [...state.events, event];
          let activeBatch = state.activeBatch;
          if (
            activeBatch &&
            event.scope === "BATCH" &&
            typeof event.stage === "string"
          ) {
            activeBatch = {
              ...activeBatch,
              state: { ...activeBatch.state, stage: event.stage },
            };
          }
          return { events, activeBatch };
        });
      },
      () => {
        eventSource = null;
        connectedBatchId = null;
        void get().fetchBatches();
        if (get().activeBatchId === batchId) {
          void get().selectBatch(batchId);
        }
      },
      () => {
        // Native EventSource reconnects automatically. The explicit replay
        // cursor covers fresh connections after page/store transitions.
      },
    );
  },

  disconnectSSE: () => {
    eventSource?.close();
    eventSource = null;
    connectedBatchId = null;
  },

  reset: () => {
    get().disconnectSSE();
    set({
      activeBatchId: null,
      activeBatch: null,
      comparison: null,
      comparisonEquity: null,
      loading: false,
      error: null,
      events: [],
    });
  },
}));
