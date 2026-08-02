import { create } from "zustand";
import { authHeaders, withAuthQuery } from "@/lib/apiAuth";

// ── Types ──

export interface FundRotationDefaults {
  params: Record<string, number>;
  schema_version: string;
  mode: string;
}

export interface FundRotationRun {
  run_id: string;
  stage: string;
  created_at?: string;
  params_fingerprint?: string;
  summary?: Record<string, unknown>;
}

export interface FundRotationState {
  defaults: FundRotationDefaults | null;
  runs: FundRotationRun[];
  activeRunId: string | null;
  activeRun: FundRotationRun | null;
  loading: boolean;
  error: string | null;
  events: Array<{ seq: number; stage: string; ts: number; [k: string]: unknown }>;

  fetchDefaults: () => Promise<void>;
  fetchRuns: () => Promise<void>;
  submitBacktest: (params: Record<string, number>, idempotencyKey: string) => Promise<string>;
  selectRun: (runId: string) => void;
  connectSSE: (runId: string) => void;
  disconnectSSE: () => void;
  reset: () => void;
}

let eventSource: EventSource | null = null;

export const useFundRotation = create<FundRotationState>((set, get) => ({
  defaults: null,
  runs: [],
  activeRunId: null,
  activeRun: null,
  loading: false,
  error: null,
  events: [],

  fetchDefaults: async () => {
    try {
      const res = await fetch(withAuthQuery("/stockpred/fund-rotation/defaults"), {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ defaults: data });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : "Failed to load defaults" });
    }
  },

  fetchRuns: async () => {
    try {
      const res = await fetch(withAuthQuery("/stockpred/fund-rotation/backtests?limit=20"), {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      set({ runs: data });
    } catch (e) {
      set({ error: e instanceof Error ? e.message : "Failed to load runs" });
    }
  },

  submitBacktest: async (params, idempotencyKey) => {
    set({ loading: true, error: null });
    try {
      const res = await fetch("/stockpred/fund-rotation/backtests", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ params, idempotency_key: idempotencyKey }),
      });
      if (res.status === 409) {
        throw new Error("Idempotency conflict: same key with different parameters");
      }
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail?.message || `HTTP ${res.status}`);
      }
      const data = await res.json();
      set({ loading: false, activeRunId: data.run_id, events: [] });
      get().connectSSE(data.run_id);
      return data.run_id;
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : "Submit failed" });
      throw e;
    }
  },

  selectRun: (runId) => {
    set({ activeRunId: runId, events: [], activeRun: null });
    // Fetch run detail
    fetch(withAuthQuery(`/stockpred/fund-rotation/backtests/${runId}`), {
      headers: authHeaders(),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data) set({ activeRun: data });
      })
      .catch(() => undefined);
    get().connectSSE(runId);
  },

  connectSSE: (runId) => {
    get().disconnectSSE();
    const url = withAuthQuery(`/stockpred/fund-rotation/backtests/${runId}/events`);
    eventSource = new EventSource(url);

    eventSource.addEventListener("progress", (e) => {
      const data = JSON.parse(e.data);
      set((state) => ({
        events: [...state.events, data],
        activeRun: state.activeRun ? { ...state.activeRun, stage: data.stage } : null,
      }));
    });

    eventSource.addEventListener("done", (e) => {
      const data = JSON.parse(e.data);
      // Update stage without duplicating the event (progress already appended it)
      set((state) => ({
        activeRun: state.activeRun ? { ...state.activeRun, stage: data.stage } : null,
      }));
      get().disconnectSSE();
      get().fetchRuns();
    });

    eventSource.onerror = () => {
      // Browser auto-reconnects; fall back to polling after repeated failures
    };
  },

  disconnectSSE: () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  },

  reset: () => {
    get().disconnectSSE();
    set({ activeRunId: null, activeRun: null, events: [], error: null });
  },
}));
