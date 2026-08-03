/** Phase 5 Task 4 — hook migrated to batch SSE and strategy catalog. */

import { create } from "zustand";
import type {
  BatchDetail,
  BatchListItem,
  BatchSubmitResponse,
  ComparisonReports,
  EventEnvelope,
  StrategyDetail,
  StrategySummary,
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
} from "./api";
import type { VariantDraft } from "./StrategyVariantsEditor";
import type { StrategyBatchRequest } from "./types";

let eventSource: EventSource | null = null;

export interface FundRotationState {
  // Catalog
  catalogVersion: string;
  strategies: StrategySummary[];
  strategyDetails: Map<string, StrategyDetail>;
  catalogLoading: boolean;
  catalogError: string | null;

  // Batch
  batches: BatchListItem[];
  activeBatchId: string | null;
  activeBatch: BatchDetail | null;
  comparison: ComparisonReports | null;
  loading: boolean;
  error: string | null;
  events: EventEnvelope[];

  // Actions
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
  loading: false,
  error: null,
  events: [],

  fetchCatalog: async () => {
    set({ catalogLoading: true, catalogError: null });
    try {
      const list = await fetchStrategies();
      const details = new Map<string, StrategyDetail>();
      for (const s of list.strategies) {
        try {
          const { data } = await fetchStrategyDetail(s.strategy_id);
          details.set(s.strategy_id, data);
        } catch {
          // skip detail if unavailable; summary still shows
        }
      }
      set({
        catalogVersion: list.catalog_version,
        strategies: list.strategies,
        strategyDetails: details,
        catalogLoading: false,
      });
    } catch (e) {
      set({
        catalogError: e instanceof Error ? e.message : "Failed to load catalog",
        catalogLoading: false,
      });
    }
  },

  fetchBatches: async () => {
    try {
      const batches = await fetchBatches();
      set({ batches, error: null });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : "Failed to load batches" });
    }
  },

  submitStrategyBatch: async (variants, evaluationStart, evaluationEnd, execution, idempotencyKey) => {
    set({ loading: true, error: null });
    try {
      const request: StrategyBatchRequest = {
        schema_version: "1",
        idempotency_key: idempotencyKey,
        mode: "RESEARCH_ONLY",
        evaluation_start_date: evaluationStart,
        evaluation_end_date: evaluationEnd,
        execution,
        variants: variants.map((v) => ({
          strategy_id: v.strategyId,
          label: v.label || undefined,
          params: v.params,
        })),
      };
      const result = await submitBatch(request);
      set({ loading: false });
      await get().fetchBatches();
      return result;
    } catch (e) {
      set({
        loading: false,
        error: e instanceof Error ? e.message : "Submission failed",
      });
      throw e;
    }
  },

  selectBatch: async (batchId: string) => {
    set({ activeBatchId: batchId, activeBatch: null, comparison: null, events: [], error: null });
    try {
      const detail = await fetchBatchDetail(batchId);
      set({ activeBatch: detail });
      // Load comparison if batch succeeded
      if (
        detail.state.stage === "SUCCEEDED" ||
        detail.state.stage === "PARTIAL_SUCCEEDED"
      ) {
        try {
          const reports = await fetchBatchReports(batchId);
          set({ comparison: reports });
        } catch {
          // comparison optional
        }
      }
    } catch (e) {
      set({ error: e instanceof Error ? e.message : "Failed to load batch" });
    }
  },

  cancelActiveBatch: async () => {
    const { activeBatchId } = get();
    if (!activeBatchId) return false;
    try {
      const result = await cancelBatch(activeBatchId);
      return result.cancelled;
    } catch {
      return false;
    }
  },

  connectBatchSSE: (batchId: string) => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    const lastSeq = get().events.length > 0
      ? get().events[get().events.length - 1].seq
      : null;

    eventSource = connectBatchSSE(
      batchId,
      lastSeq,
      (event) => {
        set((state) => {
          const exists = state.events.some((e) => e.seq === event.seq);
          if (exists) return {};
          return { events: [...state.events, event as unknown as EventEnvelope] };
        });
      },
      () => {
        eventSource = null;
        get().fetchBatches();
        if (get().activeBatchId) {
          get().selectBatch(get().activeBatchId!);
        }
      },
      () => {
        eventSource = null;
      },
    );
  },

  disconnectSSE: () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  },

  reset: () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    set({
      activeBatchId: null,
      activeBatch: null,
      comparison: null,
      loading: false,
      error: null,
      events: [],
    });
  },
}));
