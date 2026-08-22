/** Fund-rotation strategy catalog, batch and read-only run API client. */

import { authHeaders, withAuthQuery } from "@/lib/apiAuth";
import type {
  BacktestDetailResponse,
  CandidatePoolResponse,
  HoldingsTimelineResponse,
  BatchDetail,
  BatchListItem,
  BatchSubmitResponse,
  CatalogListResponse,
  ComparisonEquityData,
  ComparisonReports,
  InstrumentChartResponse,
  StrategyBatchRequest,
  StrategyDetail,
  RebalanceDecisionResponse,
  RebalanceIndexResponse,
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
  if (res.status === 304) return { data: null, etag: etag ?? null };
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
      // Persisted sequence recovery remains intact after malformed rows.
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

function parseCsvRow(row: string): string[] {
  const values: string[] = [];
  let current = "";
  let quoted = false;
  for (let index = 0; index < row.length; index += 1) {
    const char = row[index];
    if (char === '"') {
      if (quoted && row[index + 1] === '"') {
        current += '"';
        index += 1;
      } else {
        quoted = !quoted;
      }
    } else if (char === "," && !quoted) {
      values.push(current);
      current = "";
    } else {
      current += char;
    }
  }
  values.push(current);
  return values;
}

function parseEquityCsv(csv: string): ComparisonEquityData | null {
  const rows = csv
    .split(/\r?\n/)
    .filter((row) => row.trim().length > 0)
    .map(parseCsvRow);
  if (rows.length < 2 || rows[0].length < 2) return null;

  const names = rows[0].slice(1);
  const dates: string[] = [];
  const series = Object.fromEntries(names.map((name) => [name, [] as number[]]));
  for (const row of rows.slice(1)) {
    if (row.length === 0 || !row[0]) continue;
    const values = row.slice(1).map(Number);
    if (
      values.length !== names.length ||
      values.some((value) => !Number.isFinite(value))
    ) {
      continue;
    }
    dates.push(row[0]);
    names.forEach((name, index) => series[name].push(values[index]));
  }
  return dates.length > 0 ? { dates, series } : null;
}

export async function fetchBatchComparisonEquity(
  batchId: string,
): Promise<ComparisonEquityData | null> {
  const res = await fetch(batchArtifactUrl(batchId, "comparison_equity.csv"), {
    headers: authHeaders(),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw await responseError(res, "fetchBatchComparisonEquity");
  return parseEquityCsv(await res.text());
}

export async function fetchBacktestDetail(
  runId: string,
  signal?: AbortSignal,
): Promise<BacktestDetailResponse> {
  const res = await fetch(
    withAuthQuery(`${BASE}/backtests/${encodeURIComponent(runId)}`),
    { headers: authHeaders(), signal },
  );
  if (!res.ok) throw await responseError(res, "fetchBacktestDetail");
  return res.json();
}

export async function fetchCandidatePool(
  runId: string,
  signal?: AbortSignal,
): Promise<CandidatePoolResponse> {
  const res = await fetch(
    withAuthQuery(
      `${BASE}/backtests/${encodeURIComponent(runId)}/candidate-pool`,
    ),
    { headers: authHeaders(), signal },
  );
  if (!res.ok) throw await responseError(res, "fetchCandidatePool");
  return res.json();
}

export async function fetchHoldingsTimeline(
  runId: string,
  signal?: AbortSignal,
): Promise<HoldingsTimelineResponse> {
  const res = await fetch(
    withAuthQuery(
      `${BASE}/backtests/${encodeURIComponent(runId)}/holdings-timeline`,
    ),
    { headers: authHeaders(), signal },
  );
  if (!res.ok) throw await responseError(res, "fetchHoldingsTimeline");
  return res.json();
}

export async function fetchRebalanceIndex(
  runId: string,
  signal?: AbortSignal,
): Promise<RebalanceIndexResponse> {
  const res = await fetch(
    withAuthQuery(`${BASE}/backtests/${encodeURIComponent(runId)}/rebalances`),
    { headers: authHeaders(), signal },
  );
  if (!res.ok) throw await responseError(res, "fetchRebalanceIndex");
  return res.json();
}

export async function fetchRebalanceDecision(
  runId: string,
  signalDate: string,
  signal?: AbortSignal,
): Promise<RebalanceDecisionResponse> {
  const res = await fetch(
    withAuthQuery(
      `${BASE}/backtests/${encodeURIComponent(runId)}/rebalances/${encodeURIComponent(signalDate)}`,
    ),
    { headers: authHeaders(), signal },
  );
  if (!res.ok) throw await responseError(res, "fetchRebalanceDecision");
  return res.json();
}

export function backtestArtifactUrl(runId: string, artifactName: string): string {
  return withAuthQuery(
    `${BASE}/backtests/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(artifactName)}`,
  );
}

export async function fetchBacktestEquity(
  runId: string,
  signal?: AbortSignal,
): Promise<ComparisonEquityData | null> {
  const res = await fetch(backtestArtifactUrl(runId, "equity.csv"), {
    headers: authHeaders(),
    signal,
  });
  if (res.status === 404) return null;
  if (!res.ok) throw await responseError(res, "fetchBacktestEquity");
  return parseEquityCsv(await res.text());
}

export function backtestChartUrl(
  runId: string,
  tsCode: string,
  limit = 500,
  startDate?: string | null,
  endDate?: string | null,
): string {
  const params = new URLSearchParams({ limit: String(limit) });
  if (startDate) params.set("start_date", startDate);
  if (endDate) params.set("end_date", endDate);
  return withAuthQuery(
    `${BASE}/backtests/${encodeURIComponent(runId)}/instruments/${encodeURIComponent(tsCode)}/chart?${params.toString()}`,
  );
}

export async function fetchInstrumentChart(
  runId: string,
  tsCode: string,
  limit = 500,
  signal?: AbortSignal,
  startDate?: string | null,
  endDate?: string | null,
): Promise<InstrumentChartResponse> {
  const res = await fetch(backtestChartUrl(runId, tsCode, limit, startDate, endDate), {
    headers: authHeaders(),
    signal,
  });
  if (!res.ok) throw await responseError(res, "fetchInstrumentChart");
  return res.json();
}
