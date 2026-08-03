/** Phase 5 Task 5 — fair strategy comparison view (§27). */

import { AlertTriangle, Medal, XCircle } from "lucide-react";
import type { ComparisonReports } from "./types";

interface Props {
  reports: ComparisonReports;
  onSelectVariant?: (variantKey: string) => void;
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(2)}%`;
}

export function StrategyComparison({ reports, onSelectVariant }: Props) {
  const { contract, ranking, excluded, quality_warnings } = reports;

  return (
    <div className="space-y-4">
      {/* Contract info */}
      <div className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        比较指纹：{contract.fingerprint.slice(0, 16)}…
      </div>

      {/* Ranking table */}
      {ranking.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2">排名</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-1 pr-2">#</th>
                  <th className="py-1 pr-2">变体</th>
                  <th className="py-1 pr-2">策略</th>
                  <th className="py-1 pr-2">质量</th>
                  <th className="py-1">年化收益</th>
                </tr>
              </thead>
              <tbody>
                {ranking.map((entry) => (
                  <tr
                    key={entry.variant_key}
                    className="border-b hover:bg-muted/50 cursor-pointer"
                    onClick={() => onSelectVariant?.(entry.variant_key)}
                  >
                    <td className="py-1 pr-2 font-medium">
                      {entry.rank === 1 && (
                        <Medal className="inline h-3 w-3 text-amber-500 mr-1" />
                      )}
                      {entry.rank}
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
                    <td className="py-1 font-mono">{fmtPct(entry.annual_return)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Quality warnings */}
      {quality_warnings.length > 0 && (
        <div className="rounded border border-amber-300 bg-amber-50 p-3 space-y-1">
          {quality_warnings.map((w) => (
            <div
              key={w.variant_key}
              className="flex items-start gap-2 text-xs text-amber-800"
            >
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span>
                <span className="font-mono">{w.variant_key}</span>: {w.message}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Excluded variants */}
      {excluded.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2 flex items-center gap-1">
            <XCircle className="h-3.5 w-3.5 text-red-500" />
            已排除
          </h4>
          <div className="space-y-1">
            {excluded.map((e) => (
              <div
                key={e.variant_key}
                className="text-xs text-muted-foreground flex gap-2"
              >
                <span className="font-mono">{e.variant_key}</span>
                <span>— {e.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
