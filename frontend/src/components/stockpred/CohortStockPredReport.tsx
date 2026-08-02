import { useEffect, useState } from "react";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import {
  api,
  type CohortAggregateMetrics,
  type CohortReturn,
  type CohortQualityReport,
  type CohortChartData,
  type CohortPeriodRow,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import {
  AlertTriangle,
  CheckCircle2,
  TrendingUp,
  BarChart3,
  Activity,
  ShieldCheck,
} from "lucide-react";

type CohortTab = "overview" | "cohorts" | "stability" | "stocks" | "quality";

interface Props {
  runId: string;
}

function MetricCard({ label, value, format }: { label: string; value: number | null; format?: "pct" | "bps" | "num" }) {
  const display = value === null ? "N/A" : format === "pct" ? `${(value * 100).toFixed(2)}%` : format === "bps" ? `${(value * 10000).toFixed(1)} bps` : value.toFixed(4);
  return (
    <div className="rounded-lg border bg-card p-3 shadow-sm">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className={cn("text-lg font-semibold", value !== null && value > 0 ? "text-green-600" : value !== null && value < 0 ? "text-red-600" : "")}>{display}</p>
    </div>
  );
}

export function CohortStockPredReport({ runId }: Props) {
  const [tab, setTab] = useState<CohortTab>("overview");
  const [metrics, setMetrics] = useState<CohortAggregateMetrics | null>(null);
  const [returns, setReturns] = useState<CohortReturn[]>([]);
  const [quality, setQuality] = useState<CohortQualityReport | null>(null);
  const [periods, setPeriods] = useState<CohortPeriodRow[]>([]);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState("");
  const [selectedCohort, setSelectedCohort] = useState("");
  const [chart, setChart] = useState<CohortChartData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setMetrics(null); setReturns([]); setQuality(null); setPeriods([]); setSymbols([]);
    setSelectedSymbol(""); setSelectedCohort(""); setChart(null); setError(null); setTab("overview");
    setLoading(true);
    Promise.all([
      api.getCohortMetrics(runId),
      api.getCohortReturns(runId),
      api.getCohortQuality(runId),
    ])
      .then(([m, r, q]) => {
        if (!active) return;
        setMetrics(m);
        setReturns(r);
        setQuality(q);
        setSelectedCohort(r[0]?.cohort_id ?? "");
      })
      .catch((e) => active && setError(e.message || "Failed to load cohort data"))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [runId]);

  useEffect(() => {
    if (tab !== "stability" || periods.length) return;
    let active = true;
    api.getCohortPeriodBreakdown(runId).then((value) => active && setPeriods(value)).catch((e) => active && setError(e.message));
    return () => { active = false; };
  }, [tab, runId, periods.length]);

  useEffect(() => {
    if (tab !== "stocks" || symbols.length) return;
    let active = true;
    api.getCohortSymbols(runId).then((value) => {
      if (!active) return;
      setSymbols(value.symbols);
      setSelectedSymbol(value.symbols[0] ?? "");
    }).catch((e) => active && setError(e.message));
    return () => { active = false; };
  }, [tab, runId, symbols.length]);

  useEffect(() => {
    if (tab !== "stocks" || !selectedSymbol) return;
    let active = true;
    setChart(null);
    api.getCohortChart(runId, selectedSymbol).then((value) => active && setChart(value)).catch((e) => active && setError(e.message));
    return () => { active = false; };
  }, [tab, runId, selectedSymbol]);

  if (loading) return <div className="flex items-center justify-center p-8 text-muted-foreground">Loading cohort data...</div>;
  if (error) return <div className="flex items-center gap-2 p-4 text-red-600"><AlertTriangle className="h-4 w-4" />{error}</div>;
  if (!metrics) return null;

  const tabs: { id: CohortTab; label: string; icon: React.ReactNode }[] = [
    { id: "overview", label: "Overview", icon: <TrendingUp className="h-4 w-4" /> },
    { id: "cohorts", label: "Cohorts", icon: <BarChart3 className="h-4 w-4" /> },
    { id: "stability", label: "Stability", icon: <Activity className="h-4 w-4" /> },
    { id: "stocks", label: "Stocks", icon: <TrendingUp className="h-4 w-4" /> },
    { id: "quality", label: "Data Quality", icon: <ShieldCheck className="h-4 w-4" /> },
  ];

  return (
    <div className="space-y-4 p-4">
      {/* Tab bar */}
      <div className="flex gap-1 border-b">
        {tabs.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 transition-colors",
              tab === t.id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
            )}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {tab === "overview" && <OverviewTab metrics={metrics} quality={quality} />}
      {tab === "cohorts" && <CohortsTab returns={returns} />}
      {tab === "stability" && <StabilityTab periods={periods} />}
      {tab === "stocks" && <StocksTab symbols={symbols} selectedSymbol={selectedSymbol} setSelectedSymbol={setSelectedSymbol} selectedCohort={selectedCohort} setSelectedCohort={setSelectedCohort} returns={returns} chart={chart} />}
      {tab === "quality" && <QualityTab quality={quality} metrics={metrics} />}
    </div>
  );
}

// Fatal failures indicating truly invalid results (zero usable data)
const FATAL_FAILURES = new Set(["no_valid_cohorts"]);

function OverviewTab({ metrics, quality }: { metrics: CohortAggregateMetrics; quality: CohortQualityReport | null }) {
  const fatalFailures = quality?.failures.filter((f) => FATAL_FAILURES.has(f)) ?? [];
  const qualityGates = quality?.failures.filter((f) => !FATAL_FAILURES.has(f) && f !== "pit_assurance_snapshot_only") ?? [];
  const hasPit = quality?.failures.includes("pit_assurance_snapshot_only") ?? false;
  return (
    <div className="space-y-4">
      {fatalFailures.length > 0 && (
        <div className="flex items-center gap-2 rounded-md border border-red-300 bg-red-50 p-3 text-sm text-red-800">
          <AlertTriangle className="h-4 w-4" />
          回测结果无效：{fatalFailures.join(", ")}。请检查数据完整性或重新运行。
        </div>
      )}
      {fatalFailures.length === 0 && qualityGates.length > 0 && (
        <div className="flex items-center gap-2 rounded-md border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-800">
          <AlertTriangle className="h-4 w-4" />
          回测已完成，但未满足严格排行榜质量门禁：{qualityGates.join(", ")}。结果可查看，不参与排行榜。
        </div>
      )}
      {fatalFailures.length === 0 && qualityGates.length === 0 && hasPit && (
        <div className="flex items-center gap-2 rounded-md border border-yellow-300 bg-yellow-50 p-3 text-sm text-yellow-800">
          <AlertTriangle className="h-4 w-4" />
          回测已完成；由于使用快照型可修订数据，仅供研究参考，不参与严格排行榜。
        </div>
      )}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        <MetricCard label="Mean Return" value={metrics.mean_return} format="pct" />
        <MetricCard label="Mean Excess" value={metrics.mean_excess_return} format="pct" />
        <MetricCard label="HAC SE" value={metrics.hac_se} format="pct" />
        <MetricCard label="Median Return" value={metrics.median_return} format="pct" />
        <MetricCard label="Win Rate" value={metrics.win_rate} format="pct" />
        <MetricCard label="Fill Rate" value={metrics.mean_fill_rate} format="pct" />
        <MetricCard label="Idle Cash" value={metrics.mean_idle_cash_ratio} format="pct" />
        <MetricCard label="Cost Ratio" value={metrics.mean_cost_ratio} format="bps" />
        <MetricCard label="Unliquidated" value={metrics.mean_unliquidated_ratio} format="pct" />
        <MetricCard label="Valid Cohorts" value={metrics.valid_cohort_count} format="num" />
      </div>
      {metrics.bootstrap_ci && (
        <p className="text-sm text-muted-foreground">
          95% CI: [{(metrics.bootstrap_ci.lower * 100).toFixed(2)}%, {(metrics.bootstrap_ci.upper * 100).toFixed(2)}%]
        </p>
      )}
    </div>
  );
}

function CohortsTab({ returns }: { returns: CohortReturn[] }) {
  if (returns.length === 0) return <p className="text-sm text-muted-foreground">No cohort data.</p>;

  return (
    <div className="space-y-4">
      {/* Scatter: return vs index */}
      <div className="rounded-lg border p-4">
        <h3 className="mb-2 text-sm font-medium">Cohort Returns</h3>
        <div className="flex flex-wrap gap-1">
          {returns.map((r) => (
            <div
              key={r.cohort_id}
              title={`${r.cohort_id}: ${r.committed_capital_return == null ? "N/A" : `${(r.committed_capital_return * 100).toFixed(2)}%`}`}
              className={cn(
                "h-6 w-3 rounded-sm",
                (r.committed_capital_return ?? 0) > 0 ? "bg-green-500" : (r.committed_capital_return ?? 0) < 0 ? "bg-red-500" : "bg-gray-300"
              )}
              style={{ opacity: 0.4 + Math.min(Math.abs(r.committed_capital_return ?? 0) * 10, 0.6) }}
            />
          ))}
        </div>
      </div>

      {/* Distribution summary */}
      <div className="rounded-lg border p-4">
        <h3 className="mb-2 text-sm font-medium">Return Distribution</h3>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-muted-foreground"><th>Percentile</th><th>5%</th><th>25%</th><th>Median</th><th>75%</th><th>95%</th></tr></thead>
          <tbody>
            <tr>
              <td className="font-medium">Return</td>
              {(() => {
                const sorted = returns.map(r => r.committed_capital_return).filter((value): value is number => value != null).sort((a, b) => a - b);
                const pctl = (p: number) => sorted[Math.floor(p * sorted.length)] ?? 0;
                return [0.05, 0.25, 0.5, 0.75, 0.95].map((p) => (
                  <td key={p} className={pctl(p) >= 0 ? "text-green-600" : "text-red-600"}>{(pctl(p) * 100).toFixed(2)}%</td>
                ));
              })()}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

function StabilityTab({ periods }: { periods: CohortPeriodRow[] }) {
  if (!periods.length) return <p className="text-sm text-muted-foreground">No stability data.</p>;
  return <div className="rounded-lg border p-4"><h3 className="mb-2 text-sm font-medium">Stability Summary</h3><table className="w-full text-sm"><thead><tr><th>Period</th><th>Count</th><th>Mean Return</th><th>Win Rate</th></tr></thead><tbody>{periods.map((period) => <tr key={period.period}><td>{period.period}</td><td>{period.count}</td><td>{period.mean_return == null ? "N/A" : `${(period.mean_return * 100).toFixed(2)}%`}</td><td>{period.win_rate == null ? "N/A" : `${(period.win_rate * 100).toFixed(1)}%`}</td></tr>)}</tbody></table></div>;
}

function StocksTab({ symbols, selectedSymbol, setSelectedSymbol, selectedCohort, setSelectedCohort, returns, chart }: { symbols: string[]; selectedSymbol: string; setSelectedSymbol: (value: string) => void; selectedCohort: string; setSelectedCohort: (value: string) => void; returns: CohortReturn[]; chart: CohortChartData | null }) {
  const bars = (chart?.ohlcv ?? []).map((row) => ({ time: String(row.trade_date ?? row.time ?? ""), open: Number(row.open), high: Number(row.high), low: Number(row.low), close: Number(row.close), volume: Number(row.vol ?? row.volume ?? 0) }));
  const markers = (chart?.orders ?? []).filter((order) => {
    if (selectedCohort && order.cohort_id !== selectedCohort) return false;
    const executed = order.executed_quantity ?? order.quantity ?? 0;
    return Number(executed) > 0;
  }).map((order) => ({ time: String(order.trade_date ?? ""), side: String(order.side).toUpperCase() as "BUY" | "SELL", price: Number(order.price), qty: Number(order.executed_quantity ?? order.quantity ?? 0) }));
  return <div className="space-y-3"><div className="flex gap-3"><label>Stock <select value={selectedSymbol} onChange={(event) => setSelectedSymbol(event.target.value)}>{symbols.map((symbol) => <option key={symbol}>{symbol}</option>)}</select></label><label>Cohort <select aria-label="Cohort" value={selectedCohort} onChange={(event) => setSelectedCohort(event.target.value)}>{returns.map((result) => <option key={result.cohort_id} value={result.cohort_id}>{result.cohort_id}</option>)}</select></label></div>{chart && <CandlestickChart data={bars} markers={markers} />}</div>;
}

function QualityTab({ quality, metrics }: { quality: CohortQualityReport | null; metrics: CohortAggregateMetrics }) {
  const fatalFailures = quality?.failures.filter((f) => FATAL_FAILURES.has(f)) ?? [];
  const qualityGates = quality?.failures.filter((f) => !FATAL_FAILURES.has(f) && f !== "pit_assurance_snapshot_only") ?? [];
  const hasPit = quality?.failures.includes("pit_assurance_snapshot_only") ?? false;
  return (
    <div className="space-y-4">
      <div className="rounded-lg border p-4">
        <h3 className="mb-2 flex items-center gap-2 text-sm font-medium">
          {quality?.ranking_eligible ? <CheckCircle2 className="h-4 w-4 text-green-600" /> : <AlertTriangle className="h-4 w-4 text-yellow-600" />}
          Ranking Eligibility
        </h3>
        <p className="text-sm">{quality?.ranking_eligible ? "Eligible for strict leaderboard" : "Not eligible"}</p>
        {fatalFailures.length > 0 && (
          <ul className="mt-2 list-inside list-disc text-sm text-red-600">
            {fatalFailures.map((f) => <li key={f}>{f} — 数据异常，结果无效</li>)}
          </ul>
        )}
        {qualityGates.length > 0 && (
          <ul className="mt-2 list-inside list-disc text-sm text-yellow-700">
            {qualityGates.map((f) => <li key={f}>{f} — 未满足严格排行榜质量门禁</li>)}
          </ul>
        )}
        {hasPit && (
          <ul className="mt-2 list-inside list-disc text-sm text-yellow-700">
            <li>pit_assurance_snapshot_only — 使用快照冻结数据，无完整双时态证明</li>
          </ul>
        )}
      </div>
      <div className="rounded-lg border p-4">
        <h3 className="mb-2 text-sm font-medium">Coverage</h3>
        <table className="w-full text-sm">
          <tbody>
            <tr><td className="text-muted-foreground">Valid eval ratio</td><td>{((quality?.valid_eval_ratio ?? 0) * 100).toFixed(1)}%</td></tr>
            <tr><td className="text-muted-foreground">Valid cohorts</td><td>{metrics.valid_cohort_count} / {metrics.total_cohort_count}</td></tr>
            <tr><td className="text-muted-foreground">Mean fill rate</td><td>{(metrics.mean_fill_rate * 100).toFixed(1)}%</td></tr>
            <tr><td className="text-muted-foreground">Unliquidated ratio</td><td>{(metrics.mean_unliquidated_ratio * 100).toFixed(1)}%</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}
