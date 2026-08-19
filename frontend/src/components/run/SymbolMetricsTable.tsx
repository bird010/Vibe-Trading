import { useEffect, useMemo, useRef, useState } from "react";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import { GraphSignalPanel } from "@/components/charts/GraphSignalPanel";
import type { RunData, SymbolPerformanceMetrics } from "@/lib/api";
import i18n from "@/i18n";
import { formatOptionalMetric, getMetricLabel } from "@/lib/formatters";

export type ChartPayload = Pick<
  RunData,
  "price_series" | "indicator_series" | "trade_markers" | "graph_signal_series"
>;

type MetricKey = keyof Omit<SymbolPerformanceMetrics, "symbol">;
type SortKey = "symbol" | MetricKey;
type SortDirection = "asc" | "desc";

interface Props {
  runId: string;
  metrics: SymbolPerformanceMetrics[];
  onLoadSymbol: (symbol: string) => Promise<ChartPayload>;
}

const COLUMNS: SortKey[] = [
  "symbol",
  "total_return",
  "annual_return",
  "annual_volatility",
  "max_drawdown",
  "sharpe",
  "sortino",
  "calmar",
  "win_rate",
  "profit_loss_ratio",
  "trade_count",
  "avg_holding_days",
];

function compareMetrics(a: SymbolPerformanceMetrics, b: SymbolPerformanceMetrics, key: SortKey, direction: SortDirection) {
  if (key === "symbol") {
    return a.symbol.localeCompare(b.symbol) * (direction === "asc" ? 1 : -1);
  }

  const left = a[key];
  const right = b[key];
  const leftMissing = typeof left !== "number" || !Number.isFinite(left);
  const rightMissing = typeof right !== "number" || !Number.isFinite(right);
  if (leftMissing || rightMissing) {
    if (leftMissing && rightMissing) return a.symbol.localeCompare(b.symbol);
    return leftMissing ? 1 : -1;
  }
  const comparison = left - right;
  return comparison === 0
    ? a.symbol.localeCompare(b.symbol)
    : comparison * (direction === "asc" ? 1 : -1);
}

export function SymbolMetricsTable({ runId, metrics, onLoadSymbol }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>("total_return");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [cache, setCache] = useState<Record<string, ChartPayload>>({});
  const [loading, setLoading] = useState<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const chartLoadGenerationRef = useRef(0);

  function isCurrentChartLoad(generation: number) {
    return chartLoadGenerationRef.current === generation;
  }

  useEffect(() => {
    const generation = ++chartLoadGenerationRef.current;
    setExpanded(null);
    setCache({});
    setLoading({});
    setErrors({});
    return () => {
      if (isCurrentChartLoad(generation)) chartLoadGenerationRef.current += 1;
    };
  }, [runId]);

  const sortedMetrics = useMemo(
    () => [...metrics].sort((a, b) => compareMetrics(a, b, sortKey, sortDirection)),
    [metrics, sortKey, sortDirection],
  );

  if (metrics.length === 0) return null;

  function toggleSort(nextKey: SortKey) {
    if (nextKey === sortKey) {
      setSortDirection((current) => current === "asc" ? "desc" : "asc");
      return;
    }
    setSortKey(nextKey);
    setSortDirection(nextKey === "symbol" ? "asc" : "desc");
  }

  async function toggleSymbol(symbol: string) {
    if (expanded === symbol) {
      setExpanded(null);
      return;
    }

    setExpanded(symbol);
    if (cache[symbol] || loading[symbol]) return;
    const generation = chartLoadGenerationRef.current;

    setLoading((current) => ({ ...current, [symbol]: true }));
    setErrors((current) => {
      const next = { ...current };
      delete next[symbol];
      return next;
    });
    try {
      const payload = await onLoadSymbol(symbol);
      if (!isCurrentChartLoad(generation)) return;
      setCache((current) => ({ ...current, [symbol]: payload }));
    } catch (error) {
      if (!isCurrentChartLoad(generation)) return;
      setErrors((current) => ({
        ...current,
        [symbol]: error instanceof Error ? error.message : "Unable to load chart",
      }));
    } finally {
      if (!isCurrentChartLoad(generation)) return;
      setLoading((current) => {
        const next = { ...current };
        delete next[symbol];
        return next;
      });
    }
  }

  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/30 text-left text-muted-foreground">
            {COLUMNS.map((key) => {
              const label = key === "symbol" ? i18n.t("runDetail.symbol") : getMetricLabel(key);
              const active = key === sortKey;
              const ariaSort = active ? (sortDirection === "asc" ? "ascending" : "descending") : undefined;
              return (
                <th key={key} scope="col" aria-sort={ariaSort} className="whitespace-nowrap px-3 py-2 font-medium">
                  <button
                    type="button"
                    onClick={() => toggleSort(key)}
                    className="inline-flex items-center gap-1 hover:text-foreground"
                  >
                    {label}{active ? (sortDirection === "asc" ? " ↑" : " ↓") : null}
                  </button>
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sortedMetrics.map((row) => {
            const payload = cache[row.symbol];
            const isExpanded = expanded === row.symbol;
            const bars = payload?.price_series?.[row.symbol] || [];
            const points = payload?.graph_signal_series?.[row.symbol] || [];
            return (
              <FragmentedMetricRow
                key={row.symbol}
                row={row}
                columnCount={COLUMNS.length}
                isExpanded={isExpanded}
                isLoading={!!loading[row.symbol]}
                error={errors[row.symbol]}
                bars={bars}
                payload={payload}
                points={points}
                onToggle={() => void toggleSymbol(row.symbol)}
              />
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FragmentedMetricRow({
  row,
  columnCount,
  isExpanded,
  isLoading,
  error,
  bars,
  payload,
  points,
  onToggle,
}: {
  row: SymbolPerformanceMetrics;
  columnCount: number;
  isExpanded: boolean;
  isLoading: boolean;
  error?: string;
  bars: NonNullable<ChartPayload["price_series"]>[string];
  payload?: ChartPayload;
  points: NonNullable<ChartPayload["graph_signal_series"]>[string];
  onToggle: () => void;
}) {
  return (
    <>
      <tr className="border-b last:border-0 hover:bg-muted/20">
        <td className="whitespace-nowrap px-3 py-2 font-mono text-xs">
          <button type="button" onClick={onToggle} className="hover:text-primary hover:underline" aria-expanded={isExpanded}>
            {row.symbol}
          </button>
        </td>
        {COLUMNS.slice(1).map((key) => {
          const metricKey = key as MetricKey;
          return (
            <td key={metricKey} className="whitespace-nowrap px-3 py-2 text-right tabular-nums">
              {formatOptionalMetric(metricKey, row[metricKey])}
            </td>
          );
        })}
      </tr>
      {isExpanded && (
        <tr className="border-b bg-muted/10">
          <td colSpan={columnCount} className="p-3">
            {isLoading && <p className="text-sm text-muted-foreground">Loading chart…</p>}
            {error && <p className="text-sm text-destructive">{error}</p>}
            {payload && (
              <div data-testid={`symbol-chart-${row.symbol}`} className="space-y-4">
                {bars.length > 0 ? (
                  <CandlestickChart
                    data={bars}
                    markers={payload.trade_markers?.filter((marker) => !marker.code || marker.code === row.symbol)}
                    indicators={payload.indicator_series?.[row.symbol]}
                  />
                ) : (
                  <p className="text-sm text-muted-foreground">{i18n.t("charts.noPriceData")}</p>
                )}
                {points.length > 0 && <GraphSignalPanel symbol={row.symbol} points={points} />}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}
