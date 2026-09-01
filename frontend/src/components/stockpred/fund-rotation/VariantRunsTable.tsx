import { Eye } from "lucide-react";
import type { BatchDetail, ComparisonReports } from "./types";

interface Props {
  batch: BatchDetail;
  reports?: ComparisonReports | null;
  selectedVariantKey?: string | null;
  onViewDetail: (variantKey: string, runId: string) => void;
}

const STAGE_LABELS: Record<string, string> = {
  QUEUED: "排队中",
  PREPARING_DATA: "准备数据",
  GENERATING_SIGNALS: "生成信号",
  EXECUTING: "执行回测",
  COMPUTING_METRICS: "计算指标",
  WRITING_RESULTS: "写入结果",
  SUCCEEDED: "完成",
  FAILED: "失败",
  CANCELED: "已取消",
  FAILED_INTERRUPTED: "中断",
};

function statusClass(stage: string): string {
  if (stage === "SUCCEEDED") return "text-green-700";
  if (stage === "FAILED" || stage === "FAILED_INTERRUPTED") return "text-red-700";
  if (stage === "CANCELED") return "text-gray-500";
  return "text-blue-700";
}

export function VariantRunsTable({
  batch,
  reports,
  selectedVariantKey,
  onViewDetail,
}: Props) {
  const childByVariant = new Map(
    batch.child_runs.map((child) => [child.variant_key, child]),
  );
  const rankingByVariant = new Map(
    (reports?.ranking ?? []).map((entry) => [entry.variant_key, entry]),
  );
  const excludedByVariant = new Map(
    (reports?.excluded ?? []).map((entry) => [entry.variant_key, entry.reason]),
  );

  return (
    <div className="rounded-lg border p-4 space-y-3">
      <div>
        <h3 className="text-sm font-semibold">变体运行列表</h3>
        <p className="text-xs text-muted-foreground mt-0.5">
          选择具体变体查看单次回测指标、收益曲线和 ETF 交易证据。
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs whitespace-nowrap">
          <thead>
            <tr className="border-b text-left text-muted-foreground">
              <th className="py-2 pr-3">状态</th>
              <th className="py-2 pr-3">变体</th>
              <th className="py-2 pr-3">策略</th>
              <th className="py-2 pr-3">数据起始</th>
              <th className="py-2 pr-3">决策起始</th>
              <th className="py-2 pr-3">质量/排除</th>
              <th className="py-2 pr-3">Run ID</th>
              <th className="py-2">操作</th>
            </tr>
          </thead>
          <tbody>
            {batch.resolved.variants.map((variant) => {
              const child = childByVariant.get(variant.variant_key);
              const ranking = rankingByVariant.get(variant.variant_key);
              const exclusion = excludedByVariant.get(variant.variant_key);
              const stage = child?.stage ?? variant.status ?? "QUEUED";
              const runId = child?.run_id ?? variant.run_id ?? null;
              const canView = Boolean(child && runId);
              const quality = child?.quality_status ?? ranking?.quality_status;
              const selected = selectedVariantKey === variant.variant_key;
              return (
                <tr
                  key={variant.variant_key}
                  className={`border-b ${selected ? "bg-blue-50/70" : "hover:bg-muted/40"}`}
                >
                  <td className={`py-2 pr-3 font-medium ${statusClass(stage)}`}>
                    {STAGE_LABELS[stage] ?? stage}
                  </td>
                  <td className="py-2 pr-3 font-mono">{variant.variant_key}</td>
                  <td className="py-2 pr-3">{variant.strategy_id}</td>
                  <td className="py-2 pr-3 font-mono">{variant.data_start || "—"}</td>
                  <td className="py-2 pr-3 font-mono">
                    {variant.decision_start_date || "—"}
                  </td>
                  <td className="py-2 pr-3 max-w-56 truncate" title={exclusion}>
                    {exclusion ? (
                      <span className="text-amber-700">排除：{exclusion}</span>
                    ) : quality ? (
                      <span
                        className={
                          quality === "DEGRADED"
                            ? "text-amber-700"
                            : "text-green-700"
                        }
                      >
                        {quality}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="py-2 pr-3 font-mono">
                    {runId ? `${runId.slice(0, 12)}${runId.length > 12 ? "…" : ""}` : "—"}
                  </td>
                  <td className="py-2">
                    <button
                      type="button"
                      disabled={!canView}
                      onClick={() => canView && runId && onViewDetail(variant.variant_key, runId)}
                      className="inline-flex items-center gap-1 rounded border px-2 py-1 text-blue-700 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <Eye className="h-3.5 w-3.5" />
                      {stage === "FAILED" || stage === "FAILED_INTERRUPTED"
                        ? "查看错误"
                        : canView
                          ? "查看详情"
                          : "等待运行"}
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
