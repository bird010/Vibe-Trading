import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  fetchStrategies: vi.fn(),
  fetchStrategyDetail: vi.fn(),
  submitBatch: vi.fn(),
  fetchBatches: vi.fn(),
  fetchBatchDetail: vi.fn(),
  cancelBatch: vi.fn(),
  connectBatchSSE: vi.fn(),
  fetchBatchReports: vi.fn(),
  fetchBatchComparisonEquity: vi.fn(),
  fetchBacktestEquity: vi.fn(),
}));

vi.mock("../api", () => api);

import type { BatchDetail, EventEnvelope } from "../types";
import { useFundRotation } from "../useFundRotation";

const RUNNING_DETAIL: BatchDetail = {
  batch_id: "batch-running",
  state: {
    schema_version: "2",
    stage: "RUNNING_STRATEGIES",
    batch_id: "batch-running",
    mode: "RESEARCH_ONLY",
  },
  resolved: {
    batch_id: "batch-running",
    schema_version: "1",
    mode: "RESEARCH_ONLY",
    catalog_version: "catalog",
    framework_implementation_hash: "framework",
    variants: [],
    plan: {
      data_start: "20230101",
      earliest_decision_start_date: "20240101",
      evaluation_start_date: "20240102",
      evaluation_end_date: "20241231",
      variants: [],
    },
    executed_order: [],
  },
  child_runs: [],
  mode: "RESEARCH_ONLY",
};

type SseCallbacks = {
  onEvent: (event: Record<string, unknown>) => void;
  onDone: () => void;
  onError: (event: Event) => void;
};

let callbacks: SseCallbacks | null = null;
let closeSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  callbacks = null;
  closeSpy = vi.fn();
  vi.clearAllMocks();
  api.fetchBatchDetail.mockResolvedValue(RUNNING_DETAIL);
  api.fetchBatches.mockResolvedValue([]);
  api.connectBatchSSE.mockImplementation(
    (
      _batchId: string,
      _lastEventId: number | null,
      onEvent: SseCallbacks["onEvent"],
      onDone: SseCallbacks["onDone"],
      onError: SseCallbacks["onError"],
    ) => {
      callbacks = { onEvent, onDone, onError };
      return { close: closeSpy } as unknown as EventSource;
    },
  );

  useFundRotation.setState({
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
  });
  useFundRotation.getState().disconnectSSE();
});

describe("useFundRotation SSE recovery", () => {
  it("reconnects when selecting a historical non-terminal batch", async () => {
    await useFundRotation.getState().selectBatch("batch-running");

    expect(api.fetchBatchDetail).toHaveBeenCalledWith("batch-running");
    expect(api.connectBatchSSE).toHaveBeenCalledWith(
      "batch-running",
      null,
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
    );
    expect(useFundRotation.getState().activeBatch?.state.stage).toBe(
      "RUNNING_STRATEGIES",
    );
  });

  it("passes the last persisted sequence when reconnecting", () => {
    const existingEvent: EventEnvelope = {
      schema_version: "v2",
      seq: 17,
      ts: "2026-08-04T07:00:00+08:00",
      event_type: "BATCH_STAGE",
      scope: "BATCH",
      batch_id: "batch-running",
      stage: "RUNNING_STRATEGIES",
    };
    useFundRotation.setState({
      activeBatchId: "batch-running",
      activeBatch: RUNNING_DETAIL,
      events: [existingEvent],
    });

    useFundRotation.getState().connectBatchSSE("batch-running");

    expect(api.connectBatchSSE).toHaveBeenCalledWith(
      "batch-running",
      17,
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
    );
  });

  it("deduplicates replayed events and reduces batch stage", async () => {
    await useFundRotation.getState().selectBatch("batch-running");
    expect(callbacks).not.toBeNull();

    const event: EventEnvelope = {
      schema_version: "v2",
      seq: 18,
      ts: "2026-08-04T07:01:00+08:00",
      event_type: "BATCH_STAGE",
      scope: "BATCH",
      batch_id: "batch-running",
      stage: "COMPARING",
    };
    callbacks?.onEvent(event as unknown as Record<string, unknown>);
    callbacks?.onEvent(event as unknown as Record<string, unknown>);

    const state = useFundRotation.getState();
    expect(state.events).toHaveLength(1);
    expect(state.events[0].seq).toBe(18);
    expect(state.activeBatch?.state.stage).toBe("COMPARING");
  });

  it("ignores events belonging to a previously selected batch", async () => {
    await useFundRotation.getState().selectBatch("batch-running");
    const staleCallback = callbacks?.onEvent;
    useFundRotation.setState({ activeBatchId: "another-batch" });

    const staleEvent: EventEnvelope = {
      schema_version: "v2",
      seq: 19,
      ts: "2026-08-04T07:02:00+08:00",
      event_type: "BATCH_STAGE",
      scope: "BATCH",
      batch_id: "batch-running",
      stage: "SUCCEEDED",
    };
    staleCallback?.(staleEvent as unknown as Record<string, unknown>);

    expect(useFundRotation.getState().events).toHaveLength(0);
  });
});
