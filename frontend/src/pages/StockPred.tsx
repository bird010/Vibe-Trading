import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Database,
  Loader2,
  Network,
  Play,
  PieChart,
} from "lucide-react";
import {
  api,
  type StockPredStatus,
  type StrategyBatchSummary,
  type StrategyDescriptor,
} from "@/lib/api";
import { FundRotationTab } from "@/components/stockpred/fund-rotation/FundRotationTab";

interface BacktestForm {
  start: string;
  end: string;
  mode: "parity" | "research";
  topN: number;
  evalStep: number;
}

function initialDates(): Pick<BacktestForm, "start" | "end"> {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 90);
  return {
    start: start.toISOString().slice(0, 10),
    end: end.toISOString().slice(0, 10),
  };
}

const RUNNING_POLL_MS = 5000;

type StockPredTab = "batch" | "fund-rotation" | "data";

export function StockPred() {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<StockPredTab>("batch");
  const batchStreamRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const idempotencyKeyRef = useRef<string | null>(null);

  const [status, setStatus] = useState<StockPredStatus | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [recentBatches, setRecentBatches] = useState<StrategyBatchSummary[]>([]);
  const [runningBatches, setRunningBatches] = useState<StrategyBatchSummary[]>([]);
  const [strategies, setStrategies] = useState<StrategyDescriptor[]>([]);
  const [selectedStrategies, setSelectedStrategies] = useState<string[]>([]);
  const [strategyBatch, setStrategyBatch] = useState<StrategyBatchSummary | null>(null);
  const [strategySortBy, setStrategySortBy] = useState<"sharpe" | "annual_return" | "max_drawdown" | "win_rate" | "turnover">("sharpe");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [batchStarting, setBatchStarting] = useState(false);
  const [batchError, setBatchError] = useState<string | null>(null);
  const [activeBatchId, setActiveBatchId] = useState<string | null>(null);

  const [form, setForm] = useState<BacktestForm>({
    ...initialDates(),
    mode: "parity",
    topN: 50,
    evalStep: 5,
  });

  const pollRunningBatches = useCallback(() => {
    api.listUnfinishedStrategyBatches()
      .then(setRunningBatches)
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    let active = true;

    // Load strategies (fast) — controls main page skeleton
    api.listStockPredStrategies().then((items) => {
      if (active) setStrategies(items ?? []);
    }).catch((error: unknown) => {
      if (active) setLoadError(error instanceof Error ? error.message : t("stockPred.loadError"));
    }).finally(() => {
      if (active) setLoading(false);
    });

    // Load data status (slow, reads Lance) — updates Data Status section independently
    api.getStockPredStatus().then((nextStatus) => {
      if (active) setStatus(nextStatus);
    }).catch(() => {
      // Non-blocking: status section will show "not ready" fallback
      if (active) setStatus(null);
    }).finally(() => {
      if (active) setStatusLoading(false);
    });

    api.listRecentStrategyBatches(20).then((batches) => {
      if (active) setRecentBatches(batches);
    }).catch(() => undefined);

    pollRunningBatches();
    pollTimerRef.current = setInterval(pollRunningBatches, RUNNING_POLL_MS);

    return () => {
      active = false;
      batchStreamRef.current?.close();
      batchStreamRef.current = null;
      if (pollTimerRef.current !== null) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [t, pollRunningBatches]);

  async function startStrategyBatch() {
    if (!selectedStrategies.length) return;
    setBatchStarting(true);
    setBatchError(null);
    // Generate a unique idempotency key for this launch intent
    if (!idempotencyKeyRef.current) {
      idempotencyKeyRef.current = crypto.randomUUID();
    }
    try {
      const created = await api.createStrategyBatch({
        start: form.start,
        end: form.end,
        strategy_ids: selectedStrategies,
        mode: form.mode,
        top_n: form.topN,
        eval_step: form.evalStep,
        idempotency_key: idempotencyKeyRef.current,
      });
      setBatchStarting(false);
      setActiveBatchId(created.batch_id);

      // POST succeeded - batch is created. Set up tracking immediately.
      // Create a placeholder summary so UI shows the batch exists
      const placeholder: StrategyBatchSummary = {
        batch_id: created.batch_id,
        status: "queued",
        reports: [],
      };
      setStrategyBatch(placeholder);
      setRecentBatches((prev) => {
        const exists = prev.some((b) => b.batch_id === placeholder.batch_id);
        if (exists) return prev.map((b) => b.batch_id === placeholder.batch_id ? placeholder : b);
        return [placeholder, ...prev];
      });

      batchStreamRef.current?.close();
      const source = new EventSource(api.strategyBatchStreamUrl(created.batch_id));
      batchStreamRef.current = source;
      pollRunningBatches();

      const refresh = () => api.getStrategyBatch(created.batch_id, strategySortBy)
        .then((updated) => {
          setStrategyBatch(updated);
          setRecentBatches((prev) => prev.map((b) => b.batch_id === updated.batch_id ? updated : b));
          pollRunningBatches();
        })
        .catch(() => undefined);

      source.addEventListener("progress", refresh);
      const clearActive = () => {
        setActiveBatchId(null);
        idempotencyKeyRef.current = null;
      };
      source.addEventListener("done", () => {
        void refresh();
        source.close();
        batchStreamRef.current = null;
        clearActive();
      });
      // Server-sent batch_error (e.g. stalled) is a terminal state
      source.addEventListener("batch_error", () => {
        void refresh();
        source.close();
        batchStreamRef.current = null;
        clearActive();
      });
      // Native network error: do NOT close, let browser auto-reconnect
      source.onerror = () => {
        console.debug("EventSource network error, browser will auto-reconnect");
      };

      // First GET is independent - failure doesn't mean creation failed.
      // Controlled retry: up to 2 attempts with 2s delay before relying on SSE.
      const fetchDetail = async (retriesLeft: number): Promise<void> => {
        try {
          const summary = await api.getStrategyBatch(created.batch_id, strategySortBy);
          setStrategyBatch(summary);
          setRecentBatches((prev) => prev.map((b) => b.batch_id === summary.batch_id ? summary : b));
        } catch {
          if (retriesLeft > 0) {
            await new Promise((r) => setTimeout(r, 2000));
            await fetchDetail(retriesLeft - 1);
          } else {
            console.debug("Batch detail fetch failed after retries, relying on SSE");
          }
        }
      };
      await fetchDetail(2);
    } catch (error) {
      setBatchStarting(false);
      setBatchError(error instanceof Error ? error.message : t("stockPred.batchStartError"));
    }
  }

  const parity = form.mode === "parity";
  // Disable start when there's an active batch in non-terminal state
  const hasActiveBatch = activeBatchId !== null;
  const canStart = Boolean(
    status?.ready
      && form.start
      && form.end
      && form.start <= form.end
      && selectedStrategies.length > 0
      && !batchStarting
      && !hasActiveBatch,
  );

  return (
    <div className="min-h-screen p-6 lg:p-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col gap-6">
        <header className="border-b pb-6">
          <div className="inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-xs font-medium text-muted-foreground">
            <Network className="h-3.5 w-3.5" />
            {t("stockPred.badge")}
          </div>
          <h1 className="mt-3 text-3xl font-bold tracking-tight">{t("stockPred.title")}</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">{t("stockPred.subtitle")}</p>
          {/* Tab navigation — §7 */}
          <nav className="mt-4 flex gap-1 rounded-lg border p-1">
            {([
              ["batch", "策略批测", Network],
              ["fund-rotation", "基金轮动", PieChart],
              ["data", "数据状态", Database],
            ] as const).map(([id, label, Icon]) => (
              <button
                key={id}
                onClick={() => setActiveTab(id)}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  activeTab === id
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </button>
            ))}
          </nav>
        </header>

        {activeTab === "fund-rotation" && <FundRotationTab />}

        {activeTab === "batch" && (<>

        {loading ? (
          <div className="grid gap-4 md:grid-cols-2">
            {[1, 2, 3, 4].map((item) => (
              <div key={item} className="h-40 animate-pulse rounded-md border bg-muted/40" />
            ))}
          </div>
        ) : null}

        {!loading && loadError ? (
          <section className="rounded-md border border-amber-500/30 bg-amber-500/5 p-5">
            <div className="flex items-center gap-2 font-medium text-amber-700 dark:text-amber-300">
              <AlertTriangle className="h-5 w-5" />
              {t("stockPred.unavailable")}
            </div>
            <p className="mt-2 text-sm text-muted-foreground">{loadError}</p>
          </section>
        ) : null}

        {!loading && !loadError ? (
          <div className="grid gap-4 lg:grid-cols-2">
            {/* Data Status */}
            <section className="rounded-md border bg-card p-5">
              <SectionTitle icon={Database} title={t("stockPred.dataStatus")} />
              {statusLoading ? (
                <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("stockPred.loadingStatus")}
                </div>
              ) : (
                <>
                  <div className="mt-4 flex items-center gap-2">
                    {status?.ready ? (
                      <CheckCircle2 className="h-5 w-5 text-success" />
                    ) : (
                      <AlertTriangle className="h-5 w-5 text-amber-500" />
                    )}
                    <span className="font-medium">
                      {status?.ready ? t("stockPred.ready") : t("stockPred.notReady")}
                    </span>
                  </div>
                  <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                    <DataItem label={t("stockPred.contract")} value={status?.contract} />
                    <DataItem label={t("stockPred.asOf")} value={status?.as_of} />
                    <DataItem label={t("stockPred.dataRoot")} value={status?.root} wide />
                  </dl>
                  {!status?.ready && (status?.message || status?.error_code) ? (
                    <p className="mt-4 text-sm text-amber-700 dark:text-amber-300">
                      {status.message || status.error_code}
                    </p>
                  ) : null}
                </>
              )}
            </section>

            {/* Running Batches */}
            <section className="rounded-md border bg-card p-5">
              <SectionTitle icon={Activity} title={t("stockPred.progress")} />
              <RunningBatches batches={runningBatches} t={t} />
            </section>

            {/* Recent Runs */}
            <section className="rounded-md border bg-card p-5 lg:col-span-2">
              <SectionTitle icon={Network} title={t("stockPred.recentRuns")} />
              {recentBatches.length ? (
                <div className="mt-4 divide-y rounded-md border">
                  {recentBatches.map((batch) => {
                    const isOpen = strategyBatch?.batch_id === batch.batch_id;
                    return (
                      <div key={batch.batch_id}>
                        <button
                          type="button"
                          onClick={() => {
                            api.getStrategyBatch(batch.batch_id, strategySortBy)
                              .then((detail) => setStrategyBatch((current) => current?.batch_id === detail.batch_id ? null : detail))
                              .catch(() => undefined);
                          }}
                          className={`flex w-full items-center justify-between gap-3 px-3 py-2.5 text-sm transition text-left ${isOpen ? "bg-muted/30" : "hover:bg-muted/40"}`}
                        >
                          <span className="min-w-0">
                            <span className="block truncate font-mono text-xs">{batch.batch_id}</span>
                            <span className="text-xs text-muted-foreground">
                              {batch.screening_done}/{batch.screening_total} · {batch.status}{batch.phase ? ` · ${batch.phase}` : ""}
                            </span>
                          </span>
                          <span className="shrink-0 text-xs text-muted-foreground">{batch.created_at?.slice(0, 10) ?? ""}</span>
                        </button>
                        {isOpen && strategyBatch ? (
                          <div className="border-t px-3 py-2">
                            <div className="flex items-center justify-between mb-2">
                              <span className="text-sm text-muted-foreground">
                                {t("stockPred.runSelected")}
                                <select
                                  value={strategySortBy}
                                  onChange={(event) => {
                                    const value = event.target.value as typeof strategySortBy;
                                    setStrategySortBy(value);
                                    void api.getStrategyBatch(strategyBatch.batch_id, value).then(setStrategyBatch);
                                  }}
                                  className="ml-1 rounded border bg-background px-1 py-0.5 text-xs"
                                >
                                  <option value="sharpe">扣费后年化夏普</option>
                                  <option value="annual_return">年化收益</option>
                                  <option value="max_drawdown">最大回撤</option>
                                  <option value="win_rate">胜率</option>
                                  <option value="turnover">换手率</option>
                                </select>
                              </span>
                              <button
                                type="button"
                                onClick={() => setStrategyBatch(null)}
                                className="rounded border px-2 py-0.5 text-xs"
                              >
                                {t("stockPred.runSelected")}
                              </button>
                            </div>
                            <div className="text-xs text-muted-foreground mb-2">
                              {batch.status} · {batch.phase ?? "-"}
                              {" · "}{strategyBatch.reports.filter((r) => r.status === "success").length} / {strategyBatch.reports.filter((r) => r.status === "failed").length}
                              {strategyBatch.detail_done != null ? ` · ${strategyBatch.detail_done}/${strategyBatch.detail_total}` : ""}
                            </div>
                            <div className="max-h-80 overflow-y-auto divide-y rounded border text-sm">
                              {strategyBatch.reports.map((report) => (
                                <Link
                                  key={report.strategy_id}
                                  to={report.run_id ? `/runs/${report.run_id}` : "#"}
                                  className="flex justify-between gap-3 px-2 py-1.5 transition hover:bg-muted/40"
                                >
                                  <span className="truncate">{report.strategy_name}</span>
                                  <span className="shrink-0 text-xs">
                                    {report.status}{report.detail_status ? ` / ${report.detail_status}` : ""}
                                    {report.detail_reason ? `: ${report.detail_reason}` : ""}
                                  </span>
                                  <span className="shrink-0 text-xs text-muted-foreground">
                                    {strategySortBy}: {typeof report.metrics[strategySortBy] === "number" ? (report.metrics[strategySortBy] as number).toFixed(4) : "-"}
                                  </span>
                                </Link>
                              ))}
                            </div>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="mt-4 text-sm text-muted-foreground">{t("stockPred.noRuns")}</p>
              )}
              {strategyBatch && !recentBatches.some((b) => b.batch_id === strategyBatch.batch_id) ? (
                <div className="mt-4 rounded-md border p-3">
                  <div className="flex items-center justify-between mb-2">
                    <span className="font-medium text-sm">批次 {strategyBatch.batch_id}</span>
                    <button
                      type="button"
                      onClick={() => setStrategyBatch(null)}
                      className="rounded border px-2 py-0.5 text-xs"
                    >
                      {t("stockPred.runSelected")}
                    </button>
                  </div>
                  <label className="block text-sm text-muted-foreground mb-2">
                    {t("stockPred.runSelected")}
                    <select
                      value={strategySortBy}
                      onChange={(event) => {
                        const value = event.target.value as typeof strategySortBy;
                        setStrategySortBy(value);
                        void api.getStrategyBatch(strategyBatch.batch_id, value).then(setStrategyBatch);
                      }}
                      className="ml-1 rounded border bg-background px-1 py-0.5 text-xs"
                    >
                      <option value="sharpe">扣费后年化夏普</option>
                      <option value="annual_return">年化收益</option>
                      <option value="max_drawdown">最大回撤</option>
                      <option value="win_rate">胜率</option>
                      <option value="turnover">换手率</option>
                    </select>
                  </label>
                  <div className="text-xs text-muted-foreground mb-2">
                    {strategyBatch.status} · {strategyBatch.phase ?? "-"}
                    {" · "}{strategyBatch.reports.filter((r) => r.status === "success").length} / {strategyBatch.reports.filter((r) => r.status === "failed").length}
                    {strategyBatch.detail_done != null ? ` · ${strategyBatch.detail_done}/${strategyBatch.detail_total}` : ""}
                  </div>
                  <div className="max-h-80 overflow-y-auto divide-y rounded border text-sm">
                    {strategyBatch.reports.map((report) => (
                      <Link
                        key={report.strategy_id}
                        to={report.run_id ? `/runs/${report.run_id}` : "#"}
                        className="flex justify-between gap-3 px-2 py-1.5 transition hover:bg-muted/40"
                      >
                        <span className="truncate">{report.strategy_name}</span>
                        <span className="shrink-0 text-xs">
                          {report.status}{report.detail_status ? ` / ${report.detail_status}` : ""}
                          {report.detail_reason ? `: ${report.detail_reason}` : ""}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {strategySortBy}: {typeof report.metrics[strategySortBy] === "number" ? (report.metrics[strategySortBy] as number).toFixed(4) : "-"}
                        </span>
                      </Link>
                    ))}
                  </div>
                </div>
              ) : null}
            </section>

            {/* Unified Backtest Configuration */}
            <section className="rounded-md border bg-card p-5 lg:col-span-2">
              <SectionTitle icon={Play} title={t("stockPred.configuration")} />
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field label={t("stockPred.startDate")}>
                  <input
                    type="date"
                    value={form.start}
                    onChange={(event) => setForm({ ...form, start: event.target.value })}
                    aria-label={t("stockPred.startDate")}
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                  />
                </Field>
                <Field label={t("stockPred.endDate")}>
                  <input
                    type="date"
                    value={form.end}
                    onChange={(event) => setForm({ ...form, end: event.target.value })}
                    aria-label={t("stockPred.endDate")}
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                  />
                </Field>
                <Field label={t("stockPred.mode")}>
                  <select
                    value={form.mode}
                    onChange={(event) => setForm({ ...form, mode: event.target.value as BacktestForm["mode"] })}
                    aria-label={t("stockPred.mode")}
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                  >
                    <option value="parity">{t("stockPred.parityMode")}</option>
                    <option value="research">{t("stockPred.researchMode")}</option>
                  </select>
                </Field>
                <Field label={t("stockPred.topN")}>
                  <input
                    type="number"
                    min={1}
                    max={500}
                    value={form.topN}
                    disabled={parity}
                    onChange={(event) => setForm({ ...form, topN: Number(event.target.value) })}
                    aria-label={t("stockPred.topN")}
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                  />
                </Field>
                <Field label={t("stockPred.evalStep")}>
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={form.evalStep}
                    disabled={parity}
                    onChange={(event) => setForm({ ...form, evalStep: Number(event.target.value) })}
                    aria-label={t("stockPred.evalStep")}
                    className="w-full rounded-md border bg-background px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60"
                  />
                </Field>
              </div>

              {/* Strategy selection */}
              <div className="mt-5">
                <div className="mb-3 flex items-center gap-3 text-sm">
                  <label className="flex items-center gap-1.5">
                    <input
                      type="checkbox"
                      checked={strategies.length > 0 && selectedStrategies.length === strategies.length}
                      onChange={(event) => setSelectedStrategies(event.target.checked ? strategies.map((item) => item.id) : [])}
                    />
                    {t("stockPred.selectAll")}
                  </label>
                  <span className="text-xs text-muted-foreground">
                    {selectedStrategies.length}/{strategies.length} {t("stockPred.runSelected")}
                  </span>
                </div>
                <div className="max-h-64 overflow-y-auto rounded-md border p-2">
                  <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {strategies.map((item) => (
                      <label key={item.id} className="flex items-center gap-2 rounded border px-3 py-2 text-sm">
                        <input
                          type="checkbox"
                          checked={selectedStrategies.includes(item.id)}
                          onChange={(event) => setSelectedStrategies((current) =>
                            event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id),
                          )}
                        />
                        {item.name}
                        <span className="text-xs text-muted-foreground">{item.zoo || item.kind}</span>
                      </label>
                    ))}
                  </div>
                </div>
              </div>

              {batchError ? (
                <div className="mt-4 rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm text-red-700 dark:text-red-300">
                  {batchError}
                </div>
              ) : null}

              <button
                type="button"
                disabled={!canStart}
                onClick={() => void startStrategyBatch()}
                className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {batchStarting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {t("stockPred.start")}
              </button>
            </section>
          </div>
        ) : null}
        </>)}

        {activeTab === "data" && (
          <section className="rounded-md border bg-card p-5">
            <SectionTitle icon={Database} title={t("stockPred.dataStatus")} />
            {statusLoading ? (
              <div className="mt-4 flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                {t("stockPred.loadingStatus")}
              </div>
            ) : (
              <>
                <div className="mt-4 flex items-center gap-2">
                  {status?.ready ? (
                    <CheckCircle2 className="h-5 w-5 text-success" />
                  ) : (
                    <AlertTriangle className="h-5 w-5 text-amber-500" />
                  )}
                  <span className="font-medium">
                    {status?.ready ? t("stockPred.ready") : t("stockPred.notReady")}
                  </span>
                </div>
                <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                  <DataItem label={t("stockPred.contract")} value={status?.contract} />
                  <DataItem label={t("stockPred.asOf")} value={status?.as_of} />
                  <DataItem label={t("stockPred.dataRoot")} value={status?.root} wide />
                </dl>
                {status?.tables.length ? (
                  <div className="mt-4 divide-y rounded-md border">
                    {status.tables.map((table) => (
                      <div key={table.name} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                        <span className="font-mono">{table.name}</span>
                        <span className="text-muted-foreground">{table.max_date || table.status}</span>
                      </div>
                    ))}
                  </div>
                ) : null}
              </>
            )}
          </section>
        )}
      </div>
    </div>
  );
}

function SectionTitle({ icon: Icon, title }: { icon: typeof Activity; title: string }) {
  return (
    <h2 className="flex items-center gap-2 font-semibold">
      <Icon className="h-4 w-4 text-primary" />
      {title}
    </h2>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="space-y-1.5 text-sm">
      <span className="text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function DataItem({ label, value, wide = false }: { label: string; value?: string; wide?: boolean }) {
  return (
    <div className={wide ? "sm:col-span-2" : undefined}>
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="mt-1 break-all font-mono text-sm">{value || "-"}</dd>
    </div>
  );
}

function RunningBatches({
  batches,
  t,
}: {
  batches: StrategyBatchSummary[];
  t: (key: string) => string;
}) {
  if (!batches.length) {
    return <p className="mt-4 text-sm text-muted-foreground">{t("stockPred.noRunningBatches")}</p>;
  }
  return (
    <div className="mt-4 space-y-3">
      {batches.map((batch) => {
        const done = batch.screening_done ?? 0;
        const total = batch.screening_total ?? 0;
        const percent = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
        return (
          <div key={batch.batch_id} className="space-y-2 rounded border p-3">
            <div className="flex items-center justify-between gap-3 text-sm">
              <span className="truncate font-mono text-xs">{batch.batch_id}</span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {batch.status}{batch.phase ? ` · ${batch.phase}` : ""}
              </span>
            </div>
            <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
              <span>{done}/{total || "?"}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-muted">
              <div className="h-full bg-primary transition-all" style={{ width: `${percent}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}
