import { useEffect } from "react";
import {
  AlertTriangle,
  BarChart3,
  CandlestickChart,
  CheckCircle2,
  FileText,
  Loader2,
  X,
} from "lucide-react";
import { TradeMarkersChart } from "./TradeMarkersChart";
import { FundRotationEquityChart } from "./FundRotationEquityChart";
import { useBacktestDetail } from "./useBacktestDetail";
import type { BacktestDetailTab } from "./types";

const TABS: Array<{
  id: BacktestDetailTab;
  label: string;
  icon: typeof FileText;
}> = [
  { id: "overview", label: "概览", icon: FileText },
  { id: "equity", label: "收益曲线", icon: BarChart3 },
  { id: "chart", label: "K 线证据", icon: CandlestickChart },
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

export function BacktestDetailPanel() {
  const {
    selectedVariantKey,
    selectedRunId,
    detail,
    equity,
    activeTab,
    selectedInstrument,
    chart,
    loading,
    chartLoading,
    error,
    chartError,
    closeRun,
    selectTab,
    selectInstrument,
  } = useBacktestDetail();

  useEffect(() => {
    if (
      activeTab === "chart" &&
      selectedInstrument &&
      !chartLoading &&
      !chartError &&
      chart?.ts_code !== selectedInstrument
    ) {
      void selectInstrument(selectedInstrument);
    }
  }, [
    activeTab,
    chart?.ts_code,
    chartError,
    chartLoading,
    selectedInstrument,
    selectInstrument,
  ]);

  if (!selectedRunId) return null;

  const metrics = detail
    ? Object.entries(detail.metrics).filter(([, value]) => Number.isFinite(value))
    : [];
  const selectedSource = chart?.ohlcv_source;

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

            {!detail.result_published && detail.events.length > 0 && (
              <div>
                <h4 className="mb-2 text-sm font-medium">最近事件</h4>
                <div className="max-h-48 overflow-y-auto rounded border text-xs">
                  {detail.events.slice(-20).map((event, index) => (
                    <div key={`${String(event.seq ?? index)}-${index}`} className="border-b px-3 py-1.5 last:border-0">
                      <span className="mr-2 font-mono text-muted-foreground">
                        #{String(event.seq ?? "—")}
                      </span>
                      <span>{String(event.stage ?? event.message ?? event.event_type ?? "")}</span>
                      {event.error ? <span className="ml-2 text-red-700">{String(event.error)}</span> : null}
                    </div>
                  ))}
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

        {!loading && !error && detail && activeTab === "chart" && (
          <div className="space-y-4">
            {detail.instruments.length === 0 ? (
              <div className="rounded border bg-muted/20 px-3 py-8 text-center text-sm text-muted-foreground">
                当前运行没有可定位的 ETF 信号、订单、成交或持仓记录。
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-end gap-3">
                  <label className="flex flex-col gap-1 text-xs">
                    <span className="text-muted-foreground">ETF 标的</span>
                    <select
                      value={selectedInstrument ?? ""}
                      onChange={(event) => void selectInstrument(event.target.value)}
                      className="min-w-48 rounded border bg-background px-2 py-1.5 font-mono"
                    >
                      {detail.instruments.map((instrument) => (
                        <option key={instrument.ts_code} value={instrument.ts_code}>
                          {instrument.ts_code}
                          {instrument.has_trade ? " · 有成交" : instrument.has_signal ? " · 有信号" : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  {selectedSource && (
                    <div className="text-xs text-muted-foreground">
                      数据快照版本：{formatValue(selectedSource.version)}
                      {!selectedSource.available && selectedSource.reason
                        ? ` · K 线不可用：${selectedSource.reason}`
                        : ""}
                    </div>
                  )}
                </div>

                {chartLoading && (
                  <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    加载 K 线和交易证据…
                  </div>
                )}
                {!chartLoading && chartError && (
                  <div className="rounded border border-red-300 bg-red-50 px-3 py-3 text-sm text-red-700">
                    {chartError}
                  </div>
                )}
                {!chartLoading && !chartError && chart && (
                  <TradeMarkersChart
                    ohlcv={chart.ohlcv}
                    trades={chart.trades}
                    signals={chart.signals}
                    tsCode={chart.ts_code}
                  />
                )}
              </>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
