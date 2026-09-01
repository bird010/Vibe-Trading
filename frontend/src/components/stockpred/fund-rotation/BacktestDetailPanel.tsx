import { useEffect } from "react";
import {
  AlertTriangle,
  BarChart3,
  CandlestickChart,
  CheckCircle2,
  FileText,
  GitBranch,
  ListTree,
  Loader2,
  X,
} from "lucide-react";
import { WeeklyKlineEvidence } from "./WeeklyKlineEvidence";
import { groupYearlyEvidence, normalizeEvidenceDate } from "./weeklyEvidence";
import { FundRotationEquityChart } from "./FundRotationEquityChart";
import { ClusterIntervalChart } from "./ClusterIntervalChart";
import { useBacktestDetail } from "./useBacktestDetail";
import type {
  BacktestPeriod,
  BacktestDetailTab,
  CandidatePoolResponse,
  ComparisonEquityData,
  InstrumentChartResponse,
} from "./types";
import { RotationAnalysisTab } from "./RotationAnalysisTab";
import { readFundRotationUrl, syncFundRotationUrl } from "./deepLinks";

const TABS: Array<{
  id: BacktestDetailTab;
  label: string;
  icon: typeof FileText;
}> = [
  { id: "overview", label: "概览", icon: FileText },
  { id: "equity", label: "收益曲线", icon: BarChart3 },
  { id: "rotation_analysis", label: "轮动分析", icon: GitBranch },
  { id: "chart", label: "K 线证据", icon: CandlestickChart },
  { id: "candidate_pool", label: "基金候选池", icon: ListTree },
  { id: "cluster_interval", label: "聚类区间", icon: BarChart3 },
];

const METRIC_LABELS: Record<string, string> = {
  total_return: "总收益率",
  annual_return: "年化收益率",
  annual_volatility: "年化波动率",
  sharpe: "Sharpe",
  max_drawdown: "最大回撤",
  calmar: "Calmar",
  excess_total_return: "超额收益",
  annualized_excess_return: "年化超额收益",
  tracking_error: "跟踪误差",
  information_ratio: "Information Ratio",
  turnover: "换手率",
  trade_count: "交易次数",
};

const PERCENT_METRICS = new Set([
  "total_return",
  "annual_return",
  "annual_volatility",
  "max_drawdown",
  "excess_total_return",
  "annualized_excess_return",
  "tracking_error",
  "turnover",
]);

function formatMetric(key: string, value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (PERCENT_METRICS.has(key)) return `${(value * 100).toFixed(2)}%`;
  if (key === "trade_count") return Math.round(value).toLocaleString();
  return value.toFixed(3);
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function backtestDateRange(detail: { period: { data_start?: string | null; decision_start_date?: string | null; evaluation_start_date?: string | null; evaluation_end_date?: string | null } }) {
  const start = [detail.period.data_start, detail.period.decision_start_date, detail.period.evaluation_start_date]
    .map(normalizeEvidenceDate)
    .find((value): value is string => Boolean(value));
  const end = [detail.period.evaluation_end_date, detail.period.evaluation_start_date, detail.period.decision_start_date]
    .map(normalizeEvidenceDate)
    .find((value): value is string => Boolean(value));
  return start && end ? { start, end } : undefined;
}

export function BacktestDetailPanel() {
  const {
    selectedVariantKey,
    selectedRunId,
    detail,
    equity,
    activeTab,
    selectedInstrument,
    chart,
    selectInstrument,
    charts,
    loading,
    chartLoading,
    candidatePool,
    candidatePoolLoading,
    candidatePoolError,
    error,
    chartErrors,
    closeRun,
    selectTab,
    loadCharts,
    loadCandidatePool,
  } = useBacktestDetail();
  const urlState = readFundRotationUrl();
  const singleChartLoader = selectInstrument as ((tsCode: string) => Promise<void>) | undefined;

  useEffect(() => {
    if (
      (activeTab !== "chart" && activeTab !== "cluster_interval") ||
      !detail ||
      detail.instruments.length === 0 ||
      chartLoading
    ) return;
    if (activeTab === "chart" && singleChartLoader) {
      const tsCode = selectedInstrument ?? detail.instruments[0]?.ts_code;
      if (tsCode && !chart && !chartErrors[tsCode]) void singleChartLoader(tsCode);
      return;
    }
    if (
      detail.instruments.some((instrument) => !charts[instrument.ts_code]) &&
      Object.keys(chartErrors).length === 0
    ) {
      void loadCharts();
    }
  }, [activeTab, chart, chartErrors, charts, chartLoading, detail, loadCharts, selectedInstrument, singleChartLoader]);

  useEffect(() => {
    if (
      (activeTab === "candidate_pool" || activeTab === "cluster_interval") &&
      detail &&
      !candidatePoolLoading &&
      !candidatePool &&
      !candidatePoolError
    ) {
      void loadCandidatePool();
    }
  }, [activeTab, candidatePool, candidatePoolError, candidatePoolLoading, detail, loadCandidatePool]);

  if (!selectedRunId) return null;

  const metrics = detail
    ? Object.entries(detail.metrics).filter(([, value]) => Number.isFinite(value))
    : [];
  return (
    <section className="rounded-lg border bg-background">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b px-4 py-3">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold">单次回测详情</h3>
            {detail?.status === "SUCCEEDED" ? (
              <span className="inline-flex items-center gap-1 text-xs text-green-700">
                <CheckCircle2 className="h-3.5 w-3.5" />完成
              </span>
            ) : detail?.status ? (
              <span className="inline-flex items-center gap-1 text-xs text-amber-700">
                <AlertTriangle className="h-3.5 w-3.5" />{detail.status}
              </span>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span>变体：<span className="font-mono">{selectedVariantKey}</span></span>
            <span>Run：<span className="font-mono">{selectedRunId}</span></span>
            {detail?.strategy_id && <span>策略：{detail.strategy_id}</span>}
            {detail?.quality_status && <span>质量：{detail.quality_status}</span>}
          </div>
        </div>
        <button
          type="button"
          onClick={closeRun}
          aria-label="关闭回测详情"
          className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex gap-1 overflow-x-auto border-b px-4 pt-2">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => selectTab(tab.id)}
              className={`inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-xs ${
                activeTab === tab.id
                  ? "border-blue-600 text-blue-700"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {tab.label}
            </button>
          );
        })}
      </div>

      <div className="p-4">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            加载回测详情…
          </div>
        )}

        {!loading && error && (
          <div className="rounded border border-red-300 bg-red-50 px-3 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        {!loading && !error && detail && activeTab === "overview" && (
          <div className="space-y-5">
            {!detail.result_published && (
              <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                本次运行未形成可校验的发布结果，仅展示生命周期状态和错误信息。
              </div>
            )}
            {detail.error && (
              <div className="rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
                {detail.error}
              </div>
            )}

            <div>
              <h4 className="mb-2 text-sm font-medium">任务总览</h4>
              <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1 rounded border p-3 text-xs sm:grid-cols-[9rem_1fr_9rem_1fr]">
                <dt className="text-muted-foreground">批次 ID</dt>
                <dd className="font-mono">{formatValue(detail.batch_id)}</dd>
                <dt className="text-muted-foreground">运行模式</dt>
                <dd>{formatValue(detail.mode)}</dd>
                <dt className="text-muted-foreground">部分完成</dt>
                <dd>{detail.partial ? "是" : "否"}</dd>
              </dl>
            </div>

            {detail.events.length > 0 && (
              <div>
                <h4 className="mb-2 text-sm font-medium">执行生命周期</h4>
                <div className="max-h-64 overflow-y-auto rounded border text-xs">
                  {detail.events
                    .slice()
                    .sort((left, right) => Number(left.seq ?? 0) - Number(right.seq ?? 0))
                    .map((event, index) => (
                      <div
                        key={`${String(event.seq ?? index)}-${index}`}
                        className="border-b px-3 py-1.5 last:border-0"
                      >
                        <span className="mr-2 font-mono text-muted-foreground">
                          #{String(event.seq ?? "—")}
                        </span>
                        {event.ts ? (
                          <span className="mr-2 font-mono text-muted-foreground">
                            {String(event.ts)}
                          </span>
                        ) : null}
                        <span>{String(event.stage ?? event.message ?? event.event_type ?? "")}</span>
                        {event.message && event.stage ? (
                          <span className="ml-2">{String(event.message)}</span>
                        ) : null}
                        {event.error ? (
                          <span className="ml-2 text-red-700">{String(event.error)}</span>
                        ) : null}
                      </div>
                    ))}
                </div>
              </div>
            )}

            {metrics.length > 0 && (
              <div>
                <h4 className="mb-2 text-sm font-medium">核心指标</h4>
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
                  {metrics.map(([key, value]) => (
                    <div key={key} className="rounded border bg-muted/20 px-3 py-2">
                      <div className="text-[11px] text-muted-foreground">
                        {METRIC_LABELS[key] ?? key}
                      </div>
                      <div className="mt-1 font-mono text-sm font-medium">
                        {formatMetric(key, value)}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <h4 className="mb-2 text-sm font-medium">运行范围</h4>
                <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1 rounded border p-3 text-xs">
                  <dt className="text-muted-foreground">数据起始日</dt>
                  <dd className="font-mono">{formatValue(detail.period.data_start)}</dd>
                  <dt className="text-muted-foreground">决策起始日</dt>
                  <dd className="font-mono">{formatValue(detail.period.decision_start_date)}</dd>
                  <dt className="text-muted-foreground">评价起始日</dt>
                  <dd className="font-mono">{formatValue(detail.period.evaluation_start_date)}</dd>
                  <dt className="text-muted-foreground">评价结束日</dt>
                  <dd className="font-mono">{formatValue(detail.period.evaluation_end_date)}</dd>
                  <dt className="text-muted-foreground">结果已发布</dt>
                  <dd>{detail.result_published ? "是" : "否"}</dd>
                  <dt className="text-muted-foreground">可参与比较</dt>
                  <dd>{detail.publishable_for_comparison ? "是" : "否"}</dd>
                </dl>
              </div>

              <div>
                <h4 className="mb-2 text-sm font-medium">可复现身份</h4>
                <dl className="grid grid-cols-[9rem_1fr] gap-x-3 gap-y-1 rounded border p-3 text-xs">
                  <dt className="text-muted-foreground">策略实现哈希</dt>
                  <dd className="truncate font-mono" title={detail.identity.implementation_hash ?? ""}>
                    {formatValue(detail.identity.implementation_hash)}
                  </dd>
                  <dt className="text-muted-foreground">参数哈希</dt>
                  <dd className="truncate font-mono" title={detail.identity.resolved_config_hash ?? ""}>
                    {formatValue(detail.identity.resolved_config_hash)}
                  </dd>
                  <dt className="text-muted-foreground">数据快照</dt>
                  <dd className="truncate font-mono" title={detail.identity.snapshot_fingerprint ?? ""}>
                    {formatValue(detail.identity.snapshot_fingerprint)}
                  </dd>
                  <dt className="text-muted-foreground">Run identity</dt>
                  <dd className="truncate font-mono" title={detail.identity.run_identity_hash ?? ""}>
                    {formatValue(detail.identity.run_identity_hash)}
                  </dd>
                </dl>
              </div>
            </div>

            {Object.keys(detail.resolved_config).length > 0 && (
              <div>
                <h4 className="mb-2 text-sm font-medium">策略参数</h4>
                <div className="overflow-x-auto rounded border">
                  <table className="w-full text-xs">
                    <tbody>
                      {Object.entries(detail.resolved_config).map(([key, value]) => (
                        <tr key={key} className="border-b last:border-0">
                          <td className="w-64 bg-muted/20 px-3 py-1.5 font-mono">{key}</td>
                          <td className="px-3 py-1.5 font-mono break-all">{formatValue(value)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

          </div>
        )}

        {!loading && !error && detail && activeTab === "equity" && (
          equity ? (
            <FundRotationEquityChart equity={equity} />
          ) : (
            <div className="rounded border bg-muted/20 px-3 py-8 text-center text-sm text-muted-foreground">
              当前运行没有已发布的净值曲线。
            </div>
          )
        )}

        {!loading && !error && detail && activeTab === "rotation_analysis" && selectedRunId && (
          <RotationAnalysisTab runId={selectedRunId} />
        )}

        {!loading && !error && detail && activeTab === "candidate_pool" && (
          <CandidatePoolContent
            candidatePool={candidatePool}
            loading={candidatePoolLoading}
            error={candidatePoolError}
          />
        )}

        {!loading && !error && detail && activeTab === "cluster_interval" && (
          <ClusterIntervalContent
            equity={equity}
            candidatePool={candidatePool}
            charts={charts}
            candidatePoolLoading={candidatePoolLoading}
            chartLoading={chartLoading}
            candidatePoolError={candidatePoolError}
            chartErrors={chartErrors}
            period={detail.period}
            onRetryCharts={() => void loadCharts()}
          />
        )}

        {!loading && !error && detail && activeTab === "chart" && (
          <div className="space-y-4">
            {detail.instruments.length === 0 ? (
              <div className="rounded border bg-muted/20 px-3 py-8 text-center text-sm text-muted-foreground">
                当前运行没有可定位的 ETF 信号、订单、成交或持仓记录。
              </div>
            ) : (
              <>
                {singleChartLoader && detail.instruments.length > 1 && <label className="flex items-center gap-2 text-xs text-muted-foreground">选择 ETF <select value={selectedInstrument ?? ""} onChange={(event) => void singleChartLoader(event.target.value)} className="rounded border bg-background px-2 py-1 text-foreground">{detail.instruments.map((instrument) => <option key={instrument.ts_code} value={instrument.ts_code}>{instrument.ts_code}</option>)}</select></label>}
                <WeeklyKlineEvidence
                  years={groupYearlyEvidence(Object.values(chart ? { [chart.ts_code]: chart } : charts))}
                  charts={chart ? { [chart.ts_code]: chart } : charts}
                  chartErrors={chartErrors}
                  loading={chartLoading}
                  onRetry={() => void (singleChartLoader ? singleChartLoader(selectedInstrument ?? detail.instruments[0].ts_code) : loadCharts())}
                  fullDateRange={backtestDateRange(detail)}
                  initialEvidence={urlState.instrument && urlState.focusDate ? { tsCode: urlState.instrument, date: urlState.focusDate } : null}
                  onFocusChange={(tsCode, date) => syncFundRotationUrl({ runId: selectedRunId, tab: "chart", instrument: tsCode, focusDate: date }, "push")}
                />
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
}

function ClusterIntervalContent({
  equity,
  candidatePool,
  charts,
  candidatePoolLoading,
  chartLoading,
  candidatePoolError,
  chartErrors,
  period,
  onRetryCharts,
}: {
  equity: ComparisonEquityData | null;
  candidatePool: CandidatePoolResponse | null;
  charts: Record<string, InstrumentChartResponse>;
  candidatePoolLoading: boolean;
  chartLoading: boolean;
  candidatePoolError: string | null;
  chartErrors: Record<string, string>;
  period: BacktestPeriod;
  onRetryCharts: () => void;
}) {
  const hasCharts = Object.keys(charts).length > 0;
  if ((candidatePoolLoading && !candidatePool) || (chartLoading && !hasCharts)) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载聚类区间数据…
      </div>
    );
  }

  if (candidatePoolError) {
    return (
      <div className="rounded border border-red-300 bg-red-50 px-3 py-3 text-sm text-red-700">
        聚类区间数据加载失败：{candidatePoolError}
      </div>
    );
  }

  const errors = Object.entries(chartErrors);
  return (
    <div className="space-y-3">
      {errors.length > 0 && (
        <div className="space-y-2 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          <div>部分基金行情加载失败：</div>
          {errors.map(([tsCode, message]) => (
            <div key={tsCode}>{tsCode}：{message}</div>
          ))}
          <button
            type="button"
            onClick={onRetryCharts}
            className="rounded border border-amber-500 px-3 py-1"
          >
            重试失败项
          </button>
        </div>
      )}
      {chartLoading && hasCharts && (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          正在加载其余基金行情…
        </div>
      )}
      <ClusterIntervalChart
        equity={equity}
        candidatePool={candidatePool}
        charts={charts}
        period={period}
      />
    </div>
  );
}

function formatRatio(value: number | null): string {
  return value == null || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(2)}%`;
}

function CandidatePoolContent({
  candidatePool,
  loading,
  error,
}: {
  candidatePool: CandidatePoolResponse | null;
  loading: boolean;
  error: string | null;
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载基金候选池…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded border border-red-300 bg-red-50 px-3 py-3 text-sm text-red-700">
        {error}
      </div>
    );
  }
  if (!candidatePool || candidatePool.reclusters.length === 0) {
    return (
      <div className="rounded border bg-muted/20 px-3 py-8 text-center text-sm text-muted-foreground">
        当前运行没有基金候选池聚类结果。
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h4 className="text-sm font-medium">基金候选池</h4>
        <p className="mt-1 text-xs text-muted-foreground">
          每次重聚类展示 8 个簇的代表选择结果，不展开全部簇成员。
        </p>
      </div>
      {candidatePool.reclusters.map((recluster) => (
        <section key={recluster.week} className="rounded border">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b bg-muted/20 px-3 py-2">
            <div>
              <h5 className="text-sm font-medium">重聚类日期：{recluster.week}</h5>
              <div className="mt-1 text-xs text-muted-foreground">
                参与基金 {recluster.num_etfs.toLocaleString()} 只 · 最大簇占比 {formatRatio(recluster.max_cluster_share)} · 有效簇数量 {formatValue(recluster.effective_cluster_count)}
              </div>
            </div>
            <span className={`rounded px-2 py-1 text-xs ${recluster.overall === "REJECT" ? "bg-amber-100 text-amber-800" : "bg-green-100 text-green-800"}`}>
              门禁：{formatValue(recluster.overall)}
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="px-3 py-2">簇</th>
                  <th className="px-3 py-2">簇规模</th>
                  <th className="px-3 py-2">代表基金代码</th>
                  <th className="px-3 py-2">名称</th>
                  <th className="px-3 py-2">分类</th>
                  <th className="px-3 py-2">代表状态</th>
                  <th className="px-3 py-2">选择/排除原因</th>
                </tr>
              </thead>
              <tbody>
                {recluster.representatives.map((representative) => (
                  <tr key={`${recluster.week}-${representative.cluster_id}`} className="border-b last:border-0">
                    <td className="px-3 py-2 font-mono">{representative.cluster_id}</td>
                    <td className="px-3 py-2">{representative.cluster_size.toLocaleString()}</td>
                    <td className="px-3 py-2 font-mono">{formatValue(representative.selected_code)}</td>
                    <td className="px-3 py-2">{formatValue(representative.selected_name)}</td>
                    <td className="px-3 py-2">{formatValue(representative.selected_fund_type)}</td>
                    <td className="px-3 py-2">{representative.selected_code ? (representative.lock_maintained ? "延续" : "新选") : "未选"}</td>
                    <td className="px-3 py-2">{formatValue(representative.exclusion_reason)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}
