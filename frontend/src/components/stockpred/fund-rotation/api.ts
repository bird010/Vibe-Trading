/** Fund-rotation strategy catalog, batch and read-only run API client. */

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

async function responseError(res: Response, operation: string): Promise<Error> {
  const detail = await res.json().catch(() => ({}));
  const message =
    detail?.message ??
    detail?.detail?.message ??
    detail?.detail ??
    `${operation}: HTTP ${res.status}`;
  return new Error(typeof message === "string" ? message : JSON.stringify(message));
}

export async function fetchStrategies(): Promise<CatalogListResponse> {
  const res = await fetch(withAuthQuery(`${BASE}/strategies`), {
    headers: authHeaders(),
  });
  if (!res.ok) throw await responseError(res, "fetchStrategies");
  return res.json();
}

export async function fetchStrategyDetail(
  strategyId: string,
  etag?: string,
): Promise<{ data: StrategyDetail | null; etag: string | null }> {
  const headers: Record<string, string> = authHeaders();
  if (etag) headers["If-None-Match"] = etag;
  const res = await fetch(withAuthQuery(`${BASE}/strategies/${strategyId}`), {
    headers,
  });
  if (res.status === 304) {
    return { data: null, etag: etag ?? null };
  }
  if (!res.ok) throw await responseError(res, "fetchStrategyDetail");
  return {
    data: await res.json(),
    etag: res.headers.get("ETag"),
  };
}

export async function submitBatch(
  request: StrategyBatchRequest,
): Promise<BatchSubmitResponse> {
  const res = await fetch(withAuthQuery(BATCH_BASE), {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
  if (!res.ok) throw await responseError(res, "submitBatch");
  return res.json();
}

export async function fetchBatches(limit = 50): Promise<BatchListItem[]> {
  const res = await fetch(withAuthQuery(`${BATCH_BASE}?limit=${limit}`), {
    headers: authHeaders(),
  });
  if (!res.ok) throw await responseError(res, "fetchBatches");
  return res.json();
}

export async function fetchBatchDetail(batchId: string): Promise<BatchDetail> {
  const res = await fetch(withAuthQuery(`${BATCH_BASE}/${batchId}`), {
    headers: authHeaders(),
  });
  if (!res.ok) throw await responseError(res, "fetchBatchDetail");
  return res.json();
}

export async function cancelBatch(
  batchId: string,
): Promise<{ batch_id: string; cancelled: boolean }> {
  const res = await fetch(withAuthQuery(`${BATCH_BASE}/${batchId}/cancel`), {
    method: "POST",
    headers: authHeaders(),
  });
  if (!res.ok) throw await responseError(res, "cancelBatch");
  return res.json();
}

export function connectBatchSSE(
  batchId: string,
  lastEventId: number | null,
  onEvent: (event: Record<string, unknown>) => void,
  onDone: () => void,
  onError: (err: Event) => void,
): EventSource {
  const resume = lastEventId !== null ? `?last_event_id=${lastEventId}` : "";
  const url = withAuthQuery(`${BATCH_BASE}/${batchId}/events${resume}`);
  const eventSource = new EventSource(url);

  const parseAndForward = (event: MessageEvent): void => {
    try {
      const data = JSON.parse(event.data) as Record<string, unknown>;
      onEvent(data);
    } catch {
      // Malformed server events are ignored; persisted seq recovery remains intact.
    }
  };

  eventSource.addEventListener("progress", parseAndForward);
  eventSource.addEventListener("done", (event: MessageEvent) => {
    parseAndForward(event);
    eventSource.close();
    onDone();
  });
  eventSource.onerror = onError;
  return eventSource;
}

export function batchArtifactUrl(batchId: string, artifactId: string): string {
  return withAuthQuery(`${BATCH_BASE}/${batchId}/artifacts/${artifactId}`);
}

export async function fetchBatchReports(batchId: string): Promise<ComparisonReports> {
  const res = await fetch(batchArtifactUrl(batchId, "reports.json"), {
    headers: authHeaders(),
  });
  if (!res.ok) throw await responseError(res, "fetchBatchReports");
  return res.json();
}

export async function fetchBacktestDetail(
  runId: string,
): Promise<Record<string, unknown>> {
  const res = await fetch(withAuthQuery(`${BASE}/backtests/${runId}`), {
    headers: authHeaders(),
  });
  if (!res.ok) throw await responseError(res, "fetchBacktestDetail");
  return res.json();
}

export function backtestArtifactUrl(runId: string, artifactName: string): string {
  return withAuthQuery(`${BASE}/backtests/${runId}/artifacts/${artifactName}`);
}

export function backtestChartUrl(
  runId: string,
  tsCode: string,
  limit = 500,
): string {
  return withAuthQuery(
    `${BASE}/backtests/${runId}/instruments/${tsCode}/chart?limit=${limit}`,
  );
}
