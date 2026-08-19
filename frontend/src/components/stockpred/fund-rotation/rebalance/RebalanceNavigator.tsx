import type { RebalanceIndexItem } from "../types";

interface Props {
  items: RebalanceIndexItem[];
  selectedSignalDate: string | null;
  filter: "changed" | "target_changed" | "all" | "cash" | "degraded" | "rejected";
  onFilterChange: (filter: Props["filter"]) => void;
  onSelect: (signalDate: string) => void;
}

function dateLabel(date: string): string {
  return date.length === 8 ? `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}` : date;
}

function matches(item: RebalanceIndexItem, filter: Props["filter"]): boolean {
  const executedChanged = item.executed_changed_positions ?? item.actual_changed_positions ?? item.changed_positions;
  if (filter === "changed") return executedChanged > 0;
  if (filter === "target_changed") return (item.target_changed_positions ?? 0) > 0;
  if (filter === "cash") return item.cash_target_weight === 1;
  if (filter === "degraded") return item.quality_status.toUpperCase() === "DEGRADED";
  if (filter === "rejected") return item.quality_status.toUpperCase() === "REJECTED";
  return true;
}

export function RebalanceNavigator({ items, selectedSignalDate, filter, onFilterChange, onSelect }: Props) {
  const visible = items.filter((item) => matches(item, filter));
  const selectedIndex = Math.max(0, visible.findIndex((item) => item.signal_date === selectedSignalDate));
  const selected = visible[selectedIndex] ?? visible[0];
  const previous = selectedIndex > 0 ? visible[selectedIndex - 1] : null;
  const next = selectedIndex >= 0 ? visible[selectedIndex + 1] : null;
  return (
    <div className="space-y-3 rounded border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" aria-label="上一次" disabled={!previous} onClick={() => previous && onSelect(previous.signal_date)} className="rounded border px-2 py-1 text-xs disabled:opacity-40">◀ 上一次</button>
        <div className="text-center">
          <div className="font-mono text-sm">{selected ? dateLabel(selected.signal_date) : "暂无调仓"}</div>
          {selected && <div className="mt-1 text-xs text-muted-foreground">执行状态：{selected.has_execution ? "有执行" : "无执行"}</div>}
        </div>
        <button type="button" aria-label="下一次" disabled={!next} onClick={() => next && onSelect(next.signal_date)} className="rounded border px-2 py-1 text-xs disabled:opacity-40">下一次 ▶</button>
      </div>
      <div className="flex flex-wrap gap-1">
        {(["changed", "target_changed", "all", "cash", "degraded", "rejected"] as const).map((value) => (
          <button key={value} type="button" onClick={() => onFilterChange(value)} className={`rounded px-2 py-1 text-[11px] ${filter === value ? "bg-blue-100 text-blue-700" : "border text-muted-foreground"}`}>
            {value === "changed" ? "实际换仓" : value === "target_changed" ? "目标变化" : value === "all" ? "全部" : value === "cash" ? "全现金" : value === "degraded" ? "DEGRADED" : "REJECTED"}
          </button>
        ))}
      </div>
      {selected && <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-4"><span>实际成交变化：{selected.executed_changed_positions ?? selected.actual_changed_positions ?? selected.changed_positions}</span><span>目标数：{selected.target_count}</span><span>Turnover：{selected.turnover == null ? "—" : `${(selected.turnover * 100).toFixed(1)}%`}</span><span>Quality：{selected.quality_status}</span></div>}
    </div>
  );
}
