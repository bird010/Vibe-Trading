import { useEffect, useRef, useState } from "react";
import { Play, Loader2, AlertTriangle, CheckCircle2, Database, TrendingUp } from "lucide-react";
import { useFundRotation } from "./useFundRotation";
import { authHeaders, withAuthQuery } from "@/lib/apiAuth";

const RESEARCH_WARNING = "RESEARCH_ONLY · 仅供研究，不构成投资建议";

const PARAM_LABELS: Record<string, { label: string; tip: string }> = {
  k: { label: "聚类数 K", tip: "将ETF池划分为K个风格簇" },
  top_n: { label: "动量最强簇数 N", tip: "每期选择动量最强的 N 个簇；每个选中簇内可等权持有多只 ETF" },
  momentum_window_weeks: { label: "动量窗口(周)", tip: "计算动量得分所用的回看周数" },
  recluster_interval_weeks: { label: "重聚类间隔(周)", tip: "每隔多少周重新执行一次聚类分析" },
  correlation_lookback_weeks: { label: "相关性回看(周)", tip: "计算ETF间相关性矩阵所用的历史周数" },
  min_training_weeks: { label: "最小训练周数", tip: "ETF入选池所需的最少历史数据周数" },
  min_valid_weeks: { label: "最小有效周数", tip: "聚类时单个ETF所需的最少有效收益周数" },
  min_pairwise_weeks: { label: "最小配对周数", tip: "计算两两相关性所需的最少重叠周数" },
  momentum_threshold: { label: "动量阈值", tip: "动量得分低于此值的ETF不参与轮动" },
  initial_capital: { label: "初始资金(元)", tip: "回测起始资金总额" },
  commission_rate: { label: "佣金费率", tip: "每笔交易的佣金比例，如0.00025即万2.5" },
  commission_min: { label: "最低佣金(元)", tip: "单笔交易的最低佣金金额" },
  other_fee_rate: { label: "其他费率", tip: "印花税、过户费等其他交易成本比例" },
  max_participation_rate: { label: "最大参与率", tip: "单笔成交量不超过当日成交额的比例上限" },
  adv_lookback: { label: "ADV回看(天)", tip: "计算20日平均成交额所用的交易日天数" },
  base_slippage_bps: { label: "基础滑点(bps)", tip: "每笔交易的最低冲击成本(基点)" },
  max_slippage_bps: { label: "最大滑点(bps)", tip: "冲击成本上限(基点)，超过则阻断交易" },
};

const STAGE_LABELS: Record<string, string> = {
  QUEUED: "排队中",
  VALIDATING_DATA: "验证数据",
  PREPARING_RETURNS: "计算收益",
  CLUSTERING: "聚类分析",
  GENERATING_TARGETS: "生成目标",
  EXECUTING: "执行回测",
  COMPUTING_BENCHMARKS: "计算基准",
  WRITING_RESULTS: "写入结果",
  SUCCEEDED: "完成",
  FAILED: "失败",
  FAILED_INTERRUPTED: "中断",
};

export function FundRotationTab() {
  const {
    defaults,
    runs,
    activeRunId,
    activeRun,
    loading,
    error,
    events,
    fetchDefaults,
    fetchRuns,
    submitBacktest,
    selectRun,
  } = useFundRotation();

  const [params, setParams] = useState<Record<string, number>>({});
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const idempotencyRef = useRef<string | null>(null);

  useEffect(() => {
    fetchDefaults();
    fetchRuns();
  }, [fetchDefaults, fetchRuns]);

  useEffect(() => {
    if (defaults) {
      setParams(defaults.params);
    }
  }, [defaults]);

  const handleSubmit = async () => {
    // Reset idempotency key on every new submission to avoid 409 on param change
    idempotencyRef.current = crypto.randomUUID();
    const fullParams: Record<string, number | string> = {
      ...params,
      ...(startDate ? { start_date: startDate.replace(/-/g, "") } : {}),
      ...(endDate ? { end_date: endDate.replace(/-/g, "") } : {}),
    };
    try {
      await submitBacktest(fullParams as Record<string, number>, idempotencyRef.current);
    } catch {
      // Error already set in store
    }
  };

  const currentStage = activeRun?.stage ?? events[events.length - 1]?.stage ?? null;
  const isTerminal = currentStage && ["SUCCEEDED", "FAILED", "FAILED_INTERRUPTED"].includes(currentStage);

  return (
    <div className="space-y-6">
      {/* Research warning banner */}
      <div className="rounded-md border border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-800">
        {RESEARCH_WARNING}
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Left: Parameters */}
        <div className="space-y-4 rounded-lg border p-4">
          <h3 className="font-semibold text-sm">参数配置</h3>
          {/* Date range inputs */}
          <div className="grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">开始日期</span>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="rounded border px-2 py-1 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-muted-foreground">结束日期</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                className="rounded border px-2 py-1 text-sm"
              />
            </label>
          </div>
          {defaults && (
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(params).map(([key, value]) => {
                const meta = PARAM_LABELS[key];
                return (
                  <label key={key} className="flex flex-col gap-1 text-xs" title={meta?.tip ?? key}>
                    <span className="text-muted-foreground">{meta?.label ?? key}</span>
                    <input
                      type="number"
                      value={value}
                      onChange={(e) => setParams((p) => ({ ...p, [key]: Number(e.target.value) }))}
                      className="rounded border px-2 py-1 text-sm"
                      step="any"
                    />
                  </label>
                );
              })}
            </div>
          )}
          <button
            onClick={handleSubmit}
            disabled={loading}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            运行回测
          </button>
          {error && (
            <div className="flex items-center gap-2 text-sm text-red-600">
              <AlertTriangle className="h-4 w-4" />
              {error}
            </div>
          )}
        </div>

        {/* Right: Progress & Results */}
        <div className="space-y-4 lg:col-span-2">
          {/* Active run progress */}
          {activeRunId && (
            <div className="rounded-lg border p-4">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-sm">运行状态</h3>
                <span className="text-xs text-muted-foreground font-mono">{activeRunId}</span>
              </div>
              {currentStage && (
                <div className="mt-2 flex items-center gap-2">
                  {isTerminal ? (
                    currentStage === "SUCCEEDED" ? (
                      <CheckCircle2 className="h-4 w-4 text-green-600" />
                    ) : (
                      <AlertTriangle className="h-4 w-4 text-red-600" />
                    )
                  ) : (
                    <Loader2 className="h-4 w-4 animate-spin text-blue-600" />
                  )}
                  <span className="text-sm">{STAGE_LABELS[currentStage] ?? currentStage}</span>
                </div>
              )}
              {/* Event timeline */}
              {events.length > 0 && (
                <div className="mt-3 max-h-40 overflow-y-auto space-y-1">
                  {events.map((e) => (
                    <div key={e.seq} className="flex items-center gap-2 text-xs text-muted-foreground">
                      <span className="font-mono">#{e.seq}</span>
                      <span>{STAGE_LABELS[e.stage] ?? e.stage}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* History runs */}
          <div className="rounded-lg border p-4">
            <h3 className="font-semibold text-sm mb-2">历史运行</h3>
            {runs.length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无运行记录</p>
            ) : (
              <div className="space-y-1">
                {runs.map((run) => (
                  <button
                    key={run.run_id}
                    onClick={() => selectRun(run.run_id)}
                    className={`flex w-full items-center justify-between rounded px-3 py-2 text-left text-sm hover:bg-muted ${
                      run.run_id === activeRunId ? "bg-muted" : ""
                    }`}
                  >
                    <span className="font-mono text-xs">{run.run_id}</span>
                    <span className="text-xs text-muted-foreground">
                      {STAGE_LABELS[run.stage] ?? run.stage}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Results — §17.1 */}
          {currentStage === "SUCCEEDED" && activeRunId && (
            <ResultPanel runId={activeRunId} />
          )}
        </div>
      </div>
    </div>
  );
}

// ── Result Panel with tabs ──

type ResultTab = "overview" | "holdings" | "clusters" | "verification" | "diagnostics" | "quality";

function ResultPanel({ runId }: { runId: string }) {
  const [tab, setTab] = useState<ResultTab>("overview");
  const [metrics, setMetrics] = useState<Record<string, unknown> | null>(null);
  const [equityCsv, setEquityCsv] = useState<string | null>(null);

  useEffect(() => {
    // Fetch metrics.json
    fetch(withAuthQuery(`/stockpred/fund-rotation/backtests/${runId}/artifacts/metrics.json`), {
      headers: authHeaders(),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then(setMetrics)
      .catch(() => null);

    // Fetch equity.csv
    fetch(withAuthQuery(`/stockpred/fund-rotation/backtests/${runId}/artifacts/equity.csv`), {
      headers: authHeaders(),
    })
      .then((r) => (r.ok ? r.text() : null))
      .then(setEquityCsv)
      .catch(() => null);
  }, [runId]);

  const tabs: [ResultTab, string][] = [
    ["overview", "概览"],
    ["holdings", "持仓"],
    ["clusters", "聚类"],
    ["verification", "交易核验"],
    ["diagnostics", "成交诊断"],
    ["quality", "数据质量"],
  ];

  return (
    <div className="rounded-lg border p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-sm">结果</h3>
        <span className="text-xs text-muted-foreground font-mono">{runId}</span>
      </div>

      {/* Tab bar */}
      <div className="flex gap-1 border-b mb-4">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`px-3 py-1.5 text-sm font-medium border-b-2 transition ${
              tab === id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "overview" && <OverviewTab metrics={metrics} equityCsv={equityCsv} />}
      {tab === "holdings" && <ArtifactTable runId={runId} artifact="positions.csv" emptyText="暂无持仓数据" />}
      {tab === "clusters" && <ClustersTab runId={runId} />}
      {tab === "verification" && <TradeVerificationTab runId={runId} />}
      {tab === "diagnostics" && <ExecutionDiagnosticsTab runId={runId} metrics={metrics} />}
      {tab === "quality" && <DataQualityTab runId={runId} />}

      <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
        <Database className="h-3 w-3" />
        <span>数据版本已固定，结果可重放</span>
      </div>
    </div>
  );
}

function OverviewTab({ metrics, equityCsv }: { metrics: Record<string, unknown> | null; equityCsv: string | null }) {
  const strategy = (metrics?.strategy ?? {}) as Record<string, number>;

  // Parse equity CSV for chart
  const chartData = parseEquityCsv(equityCsv);

  return (
    <div className="space-y-4">
      {/* Key metrics grid */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricCard label="年化收益" value={fmtPct(strategy.annual_return)} />
        <MetricCard label="最大回撤" value={fmtPct(strategy.max_drawdown)} />
        <MetricCard label="Sharpe" value={fmtNum(strategy.sharpe)} />
        <MetricCard label="总收益" value={fmtPct(strategy.total_return)} />
      </div>

      {/* Equity curve (simple SVG sparkline) */}
      {chartData.length > 1 && (
        <div className="rounded border p-3">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="h-4 w-4 text-primary" />
            <span className="text-xs font-medium">净值曲线</span>
          </div>
          <EquityChart data={chartData} />
          <div className="mt-2 flex gap-4 text-xs text-muted-foreground">
            <span className="flex items-center gap-1"><span className="inline-block h-2 w-3 rounded-sm bg-blue-500" />策略</span>
            <span className="flex items-center gap-1"><span className="inline-block h-2 w-3 rounded-sm bg-gray-400" />等权ETF</span>
            <span className="flex items-center gap-1"><span className="inline-block h-2 w-3 rounded-sm bg-amber-500" />510300</span>
          </div>
        </div>
      )}
    </div>
  );
}

function ClustersTab({ runId }: { runId: string }) {
  const [clusters, setClusters] = useState<Array<Record<string, unknown>>>([]);

  useEffect(() => {
    fetch(withAuthQuery(`/stockpred/fund-rotation/backtests/${runId}/artifacts/clusters.csv`), {
      headers: authHeaders(),
    })
      .then((r) => (r.ok ? r.text() : null))
      .then((csv) => {
        if (!csv) return;
        setClusters(parseCsvRows(csv).slice(0, 50));
      })
      .catch(() => null);
  }, [runId]);

  if (!clusters.length) return <p className="text-sm text-muted-foreground">暂无聚类数据</p>;

  return (
    <div className="max-h-60 overflow-y-auto rounded border text-xs">
      <table className="w-full">
        <thead className="sticky top-0 bg-muted">
          <tr>
            <th className="px-2 py-1 text-left">周</th>
            <th className="px-2 py-1 text-left">ETF</th>
            <th className="px-2 py-1 text-left">簇</th>
          </tr>
        </thead>
        <tbody>
          {clusters.map((row, i) => (
            <tr key={i} className="border-t">
              <td className="px-2 py-1 font-mono">{String(row.week ?? "")}</td>
              <td className="px-2 py-1 font-mono">{String(row.ts_code ?? "")}</td>
              <td className="px-2 py-1">{String(row.cluster_id ?? "")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExecutionDiagnosticsTab({ runId, metrics }: { runId: string; metrics: Record<string, unknown> | null }) {
  const meta = (metrics?.metadata ?? {}) as Record<string, number>;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <MetricCard label="总周数" value={String(meta.num_weeks ?? "-")} />
        <MetricCard label="重聚类次数" value={String(meta.num_reclusters ?? "-")} />
        <MetricCard label="使用ETF数" value={String(meta.num_etfs_used ?? "-")} />
      </div>
      <h4 className="text-xs font-medium">订单尝试与最终状态</h4>
      <ArtifactTable runId={runId} artifact="orders.csv" emptyText="暂无订单诊断" />
      <h4 className="text-xs font-medium">逐笔成交/阻断事件</h4>
      <ArtifactTable runId={runId} artifact="trade_events.csv" emptyText="暂无成交事件" />
    </div>
  );
}

function useArtifactText(runId: string, artifact: string) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    fetch(withAuthQuery(`/stockpred/fund-rotation/backtests/${runId}/artifacts/${artifact}`), {
      headers: authHeaders(),
    })
      .then((r) => (r.ok ? r.text() : null))
      .then((value) => { if (active) setText(value); })
      .catch(() => { if (active) setText(null); });
    return () => { active = false; };
  }, [runId, artifact]);
  return text;
}

function parseCsvRows(csv: string | null): Array<Record<string, string>> {
  if (!csv?.trim()) return [];
  const records: string[][] = [];
  let record: string[] = [];
  let field = "";
  let quoted = false;
  for (let index = 0; index < csv.length; index += 1) {
    const char = csv[index];
    if (quoted) {
      if (char === '"' && csv[index + 1] === '"') {
        field += '"';
        index += 1;
      } else if (char === '"') {
        quoted = false;
      } else {
        field += char;
      }
    } else if (char === '"' && field === "") {
      quoted = true;
    } else if (char === ",") {
      record.push(field);
      field = "";
    } else if (char === "\n" || char === "\r") {
      if (char === "\r" && csv[index + 1] === "\n") index += 1;
      record.push(field);
      if (record.some((value) => value !== "")) records.push(record);
      record = [];
      field = "";
    } else {
      field += char;
    }
  }
  record.push(field);
  if (record.some((value) => value !== "")) records.push(record);
  if (records.length < 2) return [];
  const headers = records[0].map((value) => value.trim());
  return records.slice(1).map((values) => Object.fromEntries(
    headers.map((header, index) => [header || "index", values[index]?.trim() ?? ""]),
  ));
}

function ArtifactTable({ runId, artifact, emptyText }: { runId: string; artifact: string; emptyText: string }) {
  const rows = parseCsvRows(useArtifactText(runId, artifact));
  if (!rows.length) return <p className="text-sm text-muted-foreground">{emptyText}</p>;
  const columns = Object.keys(rows[0]).filter(Boolean);
  return (
    <div className="max-h-64 overflow-auto rounded border text-xs">
      <table className="min-w-full whitespace-nowrap">
        <thead className="sticky top-0 bg-muted"><tr>
          {columns.map((column) => <th key={column} className="px-2 py-1 text-left">{column}</th>)}
        </tr></thead>
        <tbody>{rows.slice(0, 300).map((row, index) => (
          <tr key={index} className="border-t">
            {columns.map((column) => <td key={column} className="px-2 py-1 font-mono">{row[column]}</td>)}
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

interface ChartPayload {
  ts_code: string;
  ohlcv: Array<Record<string, number | string>>;
  signals: Array<Record<string, number | string>>;
  trades: Array<Record<string, number | string>>;
  positions: Array<Record<string, number | string>>;
  orders: Array<Record<string, number | string>>;
}

function TradeVerificationTab({ runId }: { runId: string }) {
  const tradeRows = parseCsvRows(useArtifactText(runId, "trade_events.csv"));
  const orderRows = parseCsvRows(useArtifactText(runId, "orders.csv"));
  const signalRows = parseCsvRows(useArtifactText(runId, "targets.csv"));
  const codes = Array.from(new Set(
    [...tradeRows, ...orderRows, ...signalRows].map((row) => row.ts_code).filter(Boolean),
  )).sort();
  const [code, setCode] = useState("");
  const [chart, setChart] = useState<ChartPayload | null>(null);

  useEffect(() => {
    if (!code && codes.length) setCode(codes[0]);
  }, [code, codes]);
  useEffect(() => {
    if (!code) return;
    let active = true;
    fetch(withAuthQuery(`/stockpred/fund-rotation/backtests/${runId}/instruments/${code}/chart`), {
      headers: authHeaders(),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((value) => { if (active) setChart(value); })
      .catch(() => { if (active) setChart(null); });
    return () => { active = false; };
  }, [runId, code]);

  if (!codes.length) return <p className="text-sm text-muted-foreground">暂无可核验交易</p>;
  return (
    <div className="space-y-3">
      <label className="flex items-center gap-2 text-xs">
        <span>ETF</span>
        <select value={code} onChange={(event) => setCode(event.target.value)} className="rounded border px-2 py-1 font-mono">
          {codes.map((item) => <option key={item}>{item}</option>)}
        </select>
      </label>
      {chart && <AuditLifecycleTable payload={chart} />}
      {chart?.ohlcv?.length ? <KLineAuditChart payload={chart} /> : <p className="text-sm text-muted-foreground">暂无固定快照 K 线</p>}
      <div className="text-xs text-muted-foreground">三角标记为实际成交/阻断；标签直接显示方向与成交数量。信号不会伪装成成交。</div>
      <ArtifactTable runId={runId} artifact="trade_events.csv" emptyText="暂无成交事件" />
    </div>
  );
}

function KLineAuditChart({ payload }: { payload: ChartPayload }) {
  const bars = payload.ohlcv.slice(-120);
  const width = 760, height = 250, pad = 24;
  const values = bars.flatMap((bar) => [Number(bar.high), Number(bar.low)]).filter(Number.isFinite);
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const dateToX = new Map(bars.map((bar, index) => [String(bar.trade_date), pad + index * (width - 2 * pad) / Math.max(bars.length - 1, 1)]));
  const y = (price: number) => height - pad - (price - min) / range * (height - 2 * pad);
  const markers = payload.trades.filter((trade) =>
    dateToX.has(String(trade.trade_date)) && Number(trade.filled) > 0 && Number(trade.price) > 0,
  );
  const blocked = payload.trades.filter((trade) =>
    dateToX.has(String(trade.trade_date)) && String(trade.status) === "BLOCKED",
  );
  const adjustments = payload.trades.filter((trade) =>
    dateToX.has(String(trade.trade_date)) && String(trade.action) === "SHARE_ADJUSTMENT",
  );
  const signals = payload.signals.filter((signal) => dateToX.has(String(signal.week_ending)));
  return (
    <div className="overflow-x-auto rounded border p-2">
      <svg viewBox={`0 0 ${width} ${height}`} className="min-w-[680px] w-full" aria-label={`${payload.ts_code} K线买卖核验`}>
        {bars.map((bar, index) => {
          const x = dateToX.get(String(bar.trade_date)) ?? 0;
          const open = Number(bar.open), close = Number(bar.close), high = Number(bar.high), low = Number(bar.low);
          const up = close >= open;
          return <g key={index}>
            <line x1={x} x2={x} y1={y(high)} y2={y(low)} stroke={up ? "#dc2626" : "#16a34a"} strokeWidth="1" />
            <rect x={x - 2} y={Math.min(y(open), y(close))} width="4" height={Math.max(1, Math.abs(y(open) - y(close)))} fill={up ? "#dc2626" : "#16a34a"} />
          </g>;
        })}
        {signals.map((signal, index) => {
          const x = dateToX.get(String(signal.week_ending)) ?? 0;
          return <g key={`signal-${index}`}>
            <circle cx={x} cy={pad - 7} r="4" fill="#2563eb" />
            <text x={x + 6} y={pad + 7} fontSize="9" fill="#2563eb">{String(signal.signal_action || "TARGET")}</text>
            <text x={x + 6} y={pad - 4} fontSize="9" fill="#2563eb">目标 {Number(signal.weight).toFixed(3)}</text>
            <title>{`${signal.week_ending} 收盘后目标权重=${signal.weight}`}</title>
          </g>;
        })}
        {markers.map((trade, index) => {
          const x = dateToX.get(String(trade.trade_date)) ?? 0;
          const price = Number(trade.price);
          const buy = String(trade.action) === "BUY";
          const color = buy ? "#dc2626" : "#16a34a";
          const cy = y(price);
          return <g key={index}>
            <path d={buy ? `M${x},${cy - 8} l-5,8 h10 z` : `M${x},${cy + 8} l-5,-8 h10 z`} fill={color} />
            <text x={x + 6} y={buy ? cy - 7 : cy + 12} fontSize="9" fill={color}>{String(trade.action)} {String(trade.filled ?? 0)}</text>
            <title>{`${trade.trade_date} ${trade.action} target_weight=${trade.target_weight} requested=${trade.requested} filled=${trade.filled} unfilled=${trade.unfilled} raw_open=${trade.raw_open} price=${trade.price} fee=${trade.commission} ADV20=${trade.adv20} participation=${trade.participation_rate} post_holding=${trade.post_holding} remaining=${trade.remaining} order_id=${trade.order_id} attempt_id=${trade.attempt_id}`}</title>
          </g>;
        })}
        {blocked.map((trade, index) => {
          const x = dateToX.get(String(trade.trade_date)) ?? 0;
          const cy = pad + 12 + (index % 3) * 10;
          return <g key={`blocked-${index}`} data-marker="blocked">
            <path d={`M${x - 4},${cy - 4} L${x + 4},${cy + 4} M${x + 4},${cy - 4} L${x - 4},${cy + 4}`} stroke="#6b7280" strokeWidth="2" />
            <title>{`${trade.trade_date} BLOCKED reason=${trade.reason} requested=${trade.requested} remaining=${trade.remaining} order_id=${trade.order_id} attempt_id=${trade.attempt_id}`}</title>
          </g>;
        })}
        {adjustments.map((trade, index) => {
          const x = dateToX.get(String(trade.trade_date)) ?? 0;
          return <g key={`adjustment-${index}`} data-marker="share-adjustment">
            <circle cx={x} cy={height - pad + 8} r="4" fill="#7c3aed" />
            <title>{`${trade.trade_date} SHARE_ADJUSTMENT old_factor=${trade.old_adj_factor} new_factor=${trade.new_adj_factor} order_id=${trade.order_id}`}</title>
          </g>;
        })}
      </svg>
    </div>
  );
}

function AuditLifecycleTable({ payload }: { payload: ChartPayload }) {
  const rows: Array<Record<string, number | string>> = [
    ...(payload.orders ?? []).map((row) => ({ record_type: "PARENT_ORDER", ...row })),
    ...payload.trades
      .filter((row) => String(row.event_type || "") !== "CORPORATE_ACTION")
      .map((row) => ({ record_type: "ATTEMPT", ...row })),
  ];
  if (!rows.length) return <p className="text-sm text-muted-foreground">No order lifecycle records</p>;
  const columns = ["record_type", "order_id", "attempt_id", "trade_date", "direction", "action", "requested", "attempt_filled", "filled", "remaining", "attempt_status", "final_status", "status", "reason"];
  return <div className="max-h-64 overflow-auto rounded border text-xs" aria-label="order-attempt-lifecycle">
    <table className="min-w-full whitespace-nowrap"><thead><tr>
      {columns.map((column) => <th key={column} className="px-2 py-1 text-left">{column}</th>)}
    </tr></thead><tbody>{rows.map((row, index) => <tr key={index} className="border-t">
      {columns.map((column) => <td key={column} className="px-2 py-1 font-mono">{String(row[column] ?? "")}</td>)}
    </tr>)}</tbody></table>
  </div>;
}

function DataQualityTab({ runId }: { runId: string }) {
  const [snapshot, setSnapshot] = useState<Record<string, unknown> | null>(null);
  useEffect(() => {
    fetch(withAuthQuery(`/stockpred/fund-rotation/backtests/${runId}/artifacts/data_snapshot.json`), { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : null)).then(setSnapshot).catch(() => setSnapshot(null));
  }, [runId]);
  return (
    <div className="space-y-3">
      <div className="rounded border bg-muted/30 p-3 text-xs">
        <div className="mb-1 font-medium">不可变数据快照</div>
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap">{snapshot ? JSON.stringify(snapshot, null, 2) : "暂无快照元数据"}</pre>
      </div>
      <h4 className="text-xs font-medium">ETF 排除与数据质量原因</h4>
      <ArtifactTable runId={runId} artifact="universe.csv" emptyText="无排除记录" />
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border p-2 text-center">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-0.5 text-sm font-semibold">{value}</div>
    </div>
  );
}

function EquityChart({ data }: { data: Array<{ week: string; strategy: number; equal_weight: number; buy_hold: number }> }) {
  const width = 600;
  const height = 160;
  const padding = 4;

  const allValues = data.flatMap((d) => [d.strategy, d.equal_weight, d.buy_hold]).filter((v) => isFinite(v));
  const minVal = Math.min(...allValues);
  const maxVal = Math.max(...allValues);
  const range = maxVal - minVal || 1;

  const toPath = (key: "strategy" | "equal_weight" | "buy_hold") => {
    return data
      .map((d, i) => {
        const x = padding + (i / (data.length - 1)) * (width - 2 * padding);
        const y = height - padding - ((d[key] - minVal) / range) * (height - 2 * padding);
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  };

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
      <path d={toPath("strategy")} fill="none" stroke="#3b82f6" strokeWidth="1.5" />
      <path d={toPath("equal_weight")} fill="none" stroke="#9ca3af" strokeWidth="1" strokeDasharray="4 2" />
      <path d={toPath("buy_hold")} fill="none" stroke="#f59e0b" strokeWidth="1" strokeDasharray="4 2" />
    </svg>
  );
}

// ── Helpers ──

function parseEquityCsv(csv: string | null): Array<{ week: string; strategy: number; equal_weight: number; buy_hold: number }> {
  return parseCsvRows(csv).map((row) => {
    return {
      week: row.date ?? row.index ?? "",
      strategy: parseFloat(row.strategy) || 1,
      equal_weight: parseFloat(row.equal_weight_etf) || 1,
      buy_hold: parseFloat(row.buy_hold_510300) || 1,
    };
  });
}

function fmtPct(v: number | undefined): string {
  if (v === undefined || !isFinite(v)) return "-";
  return `${(v * 100).toFixed(2)}%`;
}

function fmtNum(v: number | undefined): string {
  if (v === undefined || !isFinite(v)) return "-";
  return v.toFixed(2);
}
