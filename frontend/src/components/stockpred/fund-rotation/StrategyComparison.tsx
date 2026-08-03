/** Fair strategy comparison with common-calendar equity curves. */

import { AlertTriangle, Medal, XCircle } from "lucide-react";
import type {
  ComparisonEquityData,
  ComparisonReports,
} from "./types";

interface Props {
  reports: ComparisonReports;
  equity?: ComparisonEquityData | null;
  onSelectVariant?: (variantKey: string) => void;
}

const CHART_WIDTH = 760;
const CHART_HEIGHT = 260;
const CHART_LEFT = 48;
const CHART_RIGHT = 18;
const CHART_TOP = 18;
const CHART_BOTTOM = 34;
const SERIES_CLASSES = [
  "stroke-blue-600",
  "stroke-emerald-600",
  "stroke-amber-600",
  "stroke-violet-600",
  "stroke-rose-600",
  "stroke-cyan-600",
  "stroke-lime-600",
  "stroke-fuchsia-600",
];
const LEGEND_CLASSES = [
  "bg-blue-600",
  "bg-emerald-600",
  "bg-amber-600",
  "bg-violet-600",
  "bg-rose-600",
  "bg-cyan-600",
  "bg-lime-600",
  "bg-fuchsia-600",
];

function fmtPct(value: number | undefined): string {
  return value === undefined || !Number.isFinite(value)
    ? "—"
    : `${(value * 100).toFixed(2)}%`;
}

function fmtNumber(value: number | undefined): string {
  return value === undefined || !Number.isFinite(value)
    ? "—"
    : value.toFixed(3);
}

function normalizedSeries(equity: ComparisonEquityData) {
  return Object.entries(equity.series)
    .slice(0, SERIES_CLASSES.length)
    .map(([name, values]) => {
      const first = values.find(
        (value) => Number.isFinite(value) && value !== 0,
      );
      return {
        name,
        values:
          first === undefined
            ? []
            : values.map((value) =>
                Number.isFinite(value) ? value / first : Number.NaN,
              ),
      };
    })
    .filter((entry) => entry.values.some(Number.isFinite));
}

function EquityComparisonChart({ equity }: { equity: ComparisonEquityData }) {
  const series = normalizedSeries(equity);
  if (equity.dates.length < 2 || series.length === 0) return null;

  const allValues = series.flatMap((entry) =>
    entry.values.filter(Number.isFinite),
  );
  const rawMin = Math.min(...allValues);
  const rawMax = Math.max(...allValues);
  const padding = Math.max((rawMax - rawMin) * 0.08, 0.01);
  const minValue = rawMin - padding;
  const maxValue = rawMax + padding;
  const valueSpan = Math.max(maxValue - minValue, 1e-9);
  const plotWidth = CHART_WIDTH - CHART_LEFT - CHART_RIGHT;
  const plotHeight = CHART_HEIGHT - CHART_TOP - CHART_BOTTOM;

  const xFor = (index: number): number =>
    CHART_LEFT +
    (plotWidth * index) / Math.max(equity.dates.length - 1, 1);
  const yFor = (value: number): number =>
    CHART_TOP + ((maxValue - value) / valueSpan) * plotHeight;

  const ticks = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    return {
      y: CHART_TOP + plotHeight * ratio,
      value: maxValue - valueSpan * ratio,
    };
  });

  return (
    <div className="space-y-2">
      <h4 className="text-sm font-semibold">统一评价日历净值曲线</h4>
      <div className="overflow-x-auto rounded border bg-background">
        <svg
          viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
          role="img"
          aria-label="Strategy comparison equity curves"
          className="min-w-[720px] w-full h-auto"
        >
          {ticks.map((tick) => (
            <g key={tick.value}>
              <line
                x1={CHART_LEFT}
                x2={CHART_WIDTH - CHART_RIGHT}
                y1={tick.y}
                y2={tick.y}
                className="stroke-border"
                strokeWidth={0.7}
              />
              <text
                x={CHART_LEFT - 6}
                y={tick.y + 3}
                textAnchor="end"
                className="fill-muted-foreground text-[9px]"
              >
                {tick.value.toFixed(2)}
              </text>
            </g>
          ))}

          <line
            x1={CHART_LEFT}
            x2={CHART_WIDTH - CHART_RIGHT}
            y1={yFor(1)}
            y2={yFor(1)}
            className="stroke-muted-foreground"
            strokeDasharray="4 4"
            strokeWidth={0.8}
          />

          {series.map((entry, seriesIndex) => {
            const points = entry.values
              .map((value, valueIndex) =>
                Number.isFinite(value)
                  ? `${xFor(valueIndex)},${yFor(value)}`
                  : null,
              )
              .filter((point): point is string => point !== null)
              .join(" ");
            return (
              <polyline
                key={entry.name}
                points={points}
                fill="none"
                className={SERIES_CLASSES[seriesIndex]}
                strokeWidth={1.8}
                strokeLinejoin="round"
                strokeLinecap="round"
              >
                <title>{entry.name}</title>
              </polyline>
            );
          })}

          <text
            x={CHART_LEFT}
            y={CHART_HEIGHT - 10}
            className="fill-muted-foreground text-[9px]"
          >
            {equity.dates[0]}
          </text>
          <text
            x={CHART_WIDTH - CHART_RIGHT}
            y={CHART_HEIGHT - 10}
            textAnchor="end"
            className="fill-muted-foreground text-[9px]"
          >
            {equity.dates[equity.dates.length - 1]}
          </text>
        </svg>
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t px-3 py-2 text-xs text-muted-foreground">
          {series.map((entry, index) => (
            <span key={entry.name} className="inline-flex items-center gap-1.5">
              <span
                className={`inline-block h-2 w-2 rounded-full ${LEGEND_CLASSES[index]}`}
              />
              <span className="font-mono">{entry.name}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

export function StrategyComparison({
  reports,
  equity,
  onSelectVariant,
}: Props) {
  const {
    contract,
    ranking,
    metrics = {},
    excluded,
    quality_warnings: qualityWarnings,
    comparison_available: comparisonAvailable = ranking.length >= 2,
    comparable_variant_count: comparableVariantCount = ranking.length,
  } = reports;

  return (
    <div className="space-y-4">
      <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground space-y-1">
        <div>比较指纹：{contract.fingerprint.slice(0, 16)}…</div>
        <div>可比较变体：{comparableVariantCount}</div>
      </div>

      {!comparisonAvailable && (
        <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          至少需要 2 个技术成功、日历一致且研究质量有效的变体，当前不生成正式排名。
        </div>
      )}

      {comparisonAvailable && equity && (
        <EquityComparisonChart equity={equity} />
      )}

      {ranking.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2">
            {comparisonAvailable ? "排名" : "可用结果"}
          </h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs whitespace-nowrap">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-1 pr-2">#</th>
                  <th className="py-1 pr-2">变体</th>
                  <th className="py-1 pr-2">策略</th>
                  <th className="py-1 pr-2">质量</th>
                  <th className="py-1 pr-2">总收益</th>
                  <th className="py-1 pr-2">年化收益</th>
                  <th className="py-1 pr-2">Sharpe</th>
                  <th className="py-1 pr-2">最大回撤</th>
                  <th className="py-1">Calmar</th>
                </tr>
              </thead>
              <tbody>
                {ranking.map((entry) => {
                  const variantMetrics = metrics[entry.variant_key] ?? {};
                  return (
                    <tr
                      key={entry.variant_key}
                      className="border-b hover:bg-muted/50 cursor-pointer"
                      onClick={() => onSelectVariant?.(entry.variant_key)}
                    >
                      <td className="py-1 pr-2 font-medium">
                        {comparisonAvailable && entry.rank === 1 && (
                          <Medal className="inline h-3 w-3 text-amber-500 mr-1" />
                        )}
                        {comparisonAvailable ? entry.rank : "—"}
                      </td>
                      <td className="py-1 pr-2 font-mono">
                        {entry.variant_key}
                      </td>
                      <td className="py-1 pr-2">{entry.strategy_id}</td>
                      <td className="py-1 pr-2">
                        <span
                          className={
                            entry.quality_status === "DEGRADED"
                              ? "text-amber-600"
                              : "text-green-600"
                          }
                        >
                          {entry.quality_status}
                        </span>
                      </td>
                      <td className="py-1 pr-2 font-mono">
                        {fmtPct(
                          entry.total_return ?? variantMetrics.total_return,
                        )}
                      </td>
                      <td className="py-1 pr-2 font-mono">
                        {fmtPct(entry.annual_return)}
                      </td>
                      <td className="py-1 pr-2 font-mono">
                        {fmtNumber(entry.sharpe ?? variantMetrics.sharpe)}
                      </td>
                      <td className="py-1 pr-2 font-mono">
                        {fmtPct(
                          entry.max_drawdown ?? variantMetrics.max_drawdown,
                        )}
                      </td>
                      <td className="py-1 font-mono">
                        {fmtNumber(entry.calmar ?? variantMetrics.calmar)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {qualityWarnings.length > 0 && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3 space-y-1">
          {qualityWarnings.map((warning) => (
            <div
              key={warning.variant_key}
              className="flex items-start gap-2 text-xs text-amber-800"
            >
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span>
                <span className="font-mono">{warning.variant_key}</span>: {warning.message}
              </span>
            </div>
          ))}
        </div>
      )}

      {excluded.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2 flex items-center gap-1">
            <XCircle className="h-3.5 w-3.5 text-red-500" />
            已排除
          </h4>
          <div className="space-y-1">
            {excluded.map((entry) => (
              <div
                key={entry.variant_key}
                className="text-xs text-muted-foreground flex gap-2"
              >
                <span className="font-mono">{entry.variant_key}</span>
                <span>— {entry.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
