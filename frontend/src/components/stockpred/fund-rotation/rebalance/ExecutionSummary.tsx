import type { PortfolioSnapshot } from "../types";

function amount(row: Record<string, unknown> | undefined): string {
  if (!row) return "—";
  const value = row.quantity ?? row.requested_quantity ?? row.original_requested_quantity ?? row.filled;
  return value === undefined || value === null ? "—" : String(value);
}

export function ExecutionSummary({ execution, before, afterTarget }: { execution: { orders: Array<Record<string, unknown>>; fills?: Array<Record<string, unknown>>; summary: { filled: number; partial: number; blocked: number; commission: number; turnover?: number | null; target_turnover?: number | null; required_turnover?: number | null; execution_turnover?: number | null; target_changed_positions?: number; required_changed_positions?: number; executed_changed_positions?: number; actual_changed_positions?: number } }; before?: PortfolioSnapshot; afterTarget?: PortfolioSnapshot }) {
  const codes = Array.from(new Set([
    ...Object.keys(before?.weights ?? {}),
    ...Object.keys(afterTarget?.weights ?? {}),
    ...execution.orders.map((order) => String(order.ts_code ?? order.code ?? "")),
    ...(execution.fills ?? []).map((fill) => String(fill.ts_code ?? fill.code ?? "")),
  ])).filter(Boolean);
  return (
    <section className="space-y-3 rounded border p-3">
      <h4 className="text-sm font-medium">③ 执行摘要</h4>
      <div className="overflow-x-auto"><table className="w-full text-xs"><thead><tr className="border-b text-left text-muted-foreground"><th className="px-2 py-1">ETF</th><th className="px-2 py-1">Before</th><th className="px-2 py-1">Target</th><th className="px-2 py-1">Order</th><th className="px-2 py-1">Fill</th><th className="px-2 py-1">状态</th></tr></thead><tbody>{codes.map((code) => { const order = execution.orders.find((row) => String(row.ts_code ?? row.code ?? "") === code); const fill = (execution.fills ?? []).find((row) => String(row.ts_code ?? row.code ?? "") === code); return <tr key={code} className="border-b last:border-0"><td className="px-2 py-1 font-mono">{code}</td><td className="px-2 py-1">{((before?.weights[code] ?? 0) * 100).toFixed(1)}%</td><td className="px-2 py-1">{((afterTarget?.weights[code] ?? 0) * 100).toFixed(1)}%</td><td className="px-2 py-1">{order ? `${String(order.action ?? order.direction ?? "—")} ${amount(order)}` : "—"}</td><td className="px-2 py-1">{amount(fill)}</td><td className="px-2 py-1">{String(fill?.status ?? order?.status ?? "—")}</td></tr>; })}</tbody></table></div>
      <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-5"><span>Orders：{execution.orders.length}</span><span>Filled：{execution.summary.filled}</span><span>Partial：{execution.summary.partial}</span><span>Blocked：{execution.summary.blocked}</span><span>Commission：¥{execution.summary.commission.toFixed(2)}</span><span>Target Turnover：{execution.summary.target_turnover == null ? "—" : `${(execution.summary.target_turnover * 100).toFixed(1)}%`}</span><span>Required Turnover：{execution.summary.required_turnover == null ? "—" : `${(execution.summary.required_turnover * 100).toFixed(1)}%`}</span><span>Execution Turnover：{execution.summary.execution_turnover == null ? "—" : `${(execution.summary.execution_turnover * 100).toFixed(1)}%`}</span><span>Required Changed：{execution.summary.required_changed_positions ?? "—"}</span><span>Executed Changed：{execution.summary.executed_changed_positions ?? execution.summary.actual_changed_positions ?? "—"}</span></div>
    </section>
  );
}
