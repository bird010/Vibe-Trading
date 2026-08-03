/** Phase 5 Task 1 — fund rotation batch API client (§18/§21). */

import { authHeaders, withAuthQuery } from "@/lib/apiAuth";
import type {
  BatchDetail,
  BatchListItem,
  BatchSubmitResponse,
  CatalogListResponse,
  ComparisonReports,
  StrategyBatchRequest,
  StrategyDetail,
} from "./types";

const BASE = "/stockpred/fund-rotation";
const BATCH_BASE = `${BASE}/strategy-batches`;

// ── Catalog ──

export async function fetchStrategies(): Promise<CatalogListResponse> {
  const res = await fetch(withAuthQuery(`${BASE}/strategies`), {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`fetchStrategies: HTTP ${res.status}`);
  return res.json();
}

export async function fetchStrategyDetail(
  strategyId: string,
  etag?: string,
): Promise<{ data: StrategyDetail; etag: string | null }> {
  const headers: Record<string, string> = authHeaders();
  if (etag) headers["If-None-Match"] = etag;
  const res = await fetch(withAuthQuery(`${BASE}/strategies/${strategyId}`), { headers });
  if (res.status === 304) {
    return { data: null as unknown as StrategyDetail, etag: etag ?? null };
  }
  if (!res.ok) throw new Error(`fetchStrategyDetail: HTTP ${res.status}`);
  return {
    data: await res.json(),
    etag: res.headers.get("ETag"),
  };
}

// ── Batch submit ──

export async function submitBatch(
  request: StrategyBatchRequest,
): Promise<BatchSubmitResponse> {
  const res = await fetch(withAuthQuery(BATCH_BASE), {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(
      detail.message || detail.detail?.message || `submitBatch: HTTP ${res.status}`,
    );
  }
  return res.json();
}

// ── Batch list ──

export async function fetchBatches(limit = 50): Promise<BatchListItem[]> {
  const res = await fetch(withAuthQuery(`${BATCH_BASE}?limit=${limit}`), {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`fetchBatches: HTTP ${res.status}`);
  return res.json();
}

// ── Batch detail ──

export async function fetchBatchDetail(batchId: string): Promise<BatchDetail> {
  const res = await fetch(withAuthQuery(`${BATCH_BASE}/${batchId}`), {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`fetchBatchDetail: HTTP ${res.status}`);
  return res.json();
}

// ── Cancel ──

export async function cancelBatch(
  batchId: string,
): Promise<{ batch_id: string; cancelled: boolean }> {
  const res = await fetch(withAuthQuery(`${BATCH_BASE}/${batchId}/cancel`), {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`cancelBatch: HTTP ${res.status}`);
  return res.json();
}

// ── SSE events ──

export function connectBatchSSE(
  batchId: string,
  _lastEventId: number | null,
  onEvent: (event: Record<string, unknown>) => void,
  onDone: () => void,
  onError: (err: Event) => void,
): EventSource {
  const url = withAuthQuery(`${BATCH_BASE}/${batchId}/events`);
  const es = new EventSource(url);

  es.addEventListener("progress", (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data) as Record<string, unknown>;
      onEvent(data);
    } catch {
      // ignore malformed events
    }
  });

  es.addEventListener("done", () => {
    es.close();
    onDone();
  });

  es.onerror = (err: Event) => {
    onError(err);
  };

  return es;
}

// ── Batch artifacts ──

export function batchArtifactUrl(batchId: string, artifactId: string): string {
  return withAuthQuery(`${BATCH_BASE}/${batchId}/artifacts/${artifactId}`);
}

export async function fetchBatchReports(batchId: string): Promise<ComparisonReports> {
  const url = batchArtifactUrl(batchId, "reports.json");
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw new Error(`fetchBatchReports: HTTP ${res.status}`);
  return res.json();
}

// ── Legacy backtest read endpoints (v1 + v2 child runs) ──

export async function fetchBacktestDetail(runId: string): Promise<Record<string, unknown>> {
  const res = await fetch(withAuthQuery(`${BASE}/backtests/${runId}`), {
    headers: authHeaders(),
  });
  if (!res.ok) throw new Error(`fetchBacktestDetail: HTTP ${res.status}`);
  return res.json();
}

export function backtestArtifactUrl(runId: string, artifactName: string): string {
  return withAuthQuery(`${BASE}/backtests/${runId}/artifacts/${artifactName}`);
}

export function backtestChartUrl(runId: string, tsCode: string, limit = 500): string {
  return withAuthQuery(
    `${BASE}/backtests/${runId}/instruments/${tsCode}/chart?limit=${limit}`,
  );
}
