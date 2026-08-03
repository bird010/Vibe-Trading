/** Fair strategy comparison view. */

import { AlertTriangle, Medal, XCircle } from "lucide-react";
import type { ComparisonReports } from "./types";

interface Props {
  reports: ComparisonReports;
  onSelectVariant?: (variantKey: string) => void;
}

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

export function StrategyComparison({ reports, onSelectVariant }: Props) {
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
                      <td className="py-1 pr-2 font-mono">{entry.variant_key}</td>
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
                        {fmtPct(entry.total_return ?? variantMetrics.total_return)}
                      </td>
                      <td className="py-1 pr-2 font-mono">
                        {fmtPct(entry.annual_return)}
                      </td>
                      <td className="py-1 pr-2 font-mono">
                        {fmtNumber(entry.sharpe ?? variantMetrics.sharpe)}
                      </td>
                      <td className="py-1 pr-2 font-mono">
                        {fmtPct(entry.max_drawdown ?? variantMetrics.max_drawdown)}
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
