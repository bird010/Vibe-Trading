import type { HoldingInterval } from "../types";

export function HoldingTooltip({ interval }: { interval: HoldingInterval }) {
  return (
    <div className="space-y-1 text-xs">
      <div className="font-medium">{interval.ts_code}</div>
      <div className="text-muted-foreground">
        {interval.start_date} ～ {interval.end_date}
      </div>
      <div>实际权重：{(interval.actual_weight * 100).toFixed(2)}%</div>
      <div>目标权重：{interval.target_weight == null ? "—" : `${(interval.target_weight * 100).toFixed(2)}%`}</div>
    </div>
  );
}
