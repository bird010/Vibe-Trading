import type { PortfolioSnapshot } from "../types";

interface Props {
  before: PortfolioSnapshot;
  afterTarget: PortfolioSnapshot;
  onInstrumentClick: (tsCode: string) => void;
}

function percent(value: number | undefined): string {
  return `${((value ?? 0) * 100).toFixed(1)}%`;
}

export function PortfolioChangeChart({ before, afterTarget, onInstrumentClick }: Props) {
  const codes = Array.from(new Set([...Object.keys(before.weights), ...Object.keys(afterTarget.weights)]))
    .sort((left, right) => Math.abs((afterTarget.weights[right] ?? 0) - (before.weights[right] ?? 0)) - Math.abs((afterTarget.weights[left] ?? 0) - (before.weights[left] ?? 0)));
  return (
    <section className="space-y-2 rounded border p-3">
      <h4 className="text-sm font-medium">① 组合变化 · Before → Target</h4>
      <div className="space-y-2">
        {codes.length === 0 && <p className="text-xs text-muted-foreground">组合为空，当前保持全现金。</p>}
        {codes.map((code) => {
          const previous = before.weights[code] ?? 0;
          const target = afterTarget.weights[code] ?? 0;
          const previousPosition = `${Math.max(0, Math.min(100, previous * 100))}%`;
          const targetPosition = `${Math.max(0, Math.min(100, target * 100))}%`;
          const lineLeft = Math.min(previous, target) * 100;
          const lineWidth = Math.abs(target - previous) * 100;
          const status = previous === 0 && target > 0 ? "NEW" : previous > 0 && target === 0 ? "DROP" : previous === target ? "KEEP" : "REBALANCE";
          const content = (
            <div className="grid w-full grid-cols-[8rem_4rem_minmax(8rem,1fr)_4rem_5rem] items-center gap-2 text-left text-xs">
              <span className="truncate font-mono">{code}</span>
              <span className="text-right text-muted-foreground">{percent(previous)}</span>
              <span className="relative h-5 border-y border-dashed border-muted-foreground/30">
                <span className="absolute top-1/2 h-0.5 -translate-y-1/2 bg-blue-200" style={{ left: `${lineLeft}%`, width: `${lineWidth}%` }} />
                <span data-testid={`before-marker-${code}`} className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-blue-700 bg-background" style={{ left: previousPosition }} />
                <span data-testid={`target-marker-${code}`} className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-emerald-700 bg-emerald-100" style={{ left: targetPosition }} />
              </span>
              <span className="text-muted-foreground">→ {percent(target)}</span>
              <span className={status === "NEW" ? "font-medium text-emerald-700" : status === "DROP" ? "font-medium text-red-700" : "text-muted-foreground"}>{status}</span>
            </div>
          );
          return code === "_CASH" ? <div key={code} aria-label="Cash position">{content}</div> : <button key={code} type="button" onClick={() => onInstrumentClick(code)} className="hover:bg-muted/40">{content}</button>;
        })}
      </div>
    </section>
  );
}
