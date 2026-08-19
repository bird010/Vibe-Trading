import type { ChangeEvent } from "react";
import { dateKeyAtTimestamp, dateTimestamp } from "./dateUtils";

interface Props {
  startDate: string;
  endDate: string;
  window: { start: string; end: string };
  onChange: (window: { start: string; end: string }) => void;
  onReset: () => void;
}

export function HoldingsTimeBrush({ startDate, endDate, window, onChange, onReset }: Props) {
  const start = dateTimestamp(startDate);
  const end = dateTimestamp(endDate);
  const span = Math.max(end - start, 1);
  const startValue = Math.round(((dateTimestamp(window.start) - start) / span) * 100);
  const endValue = Math.round(((dateTimestamp(window.end) - start) / span) * 100);
  const dateAt = (percent: number): string => {
    return dateKeyAtTimestamp(Math.round(start + (span * percent) / 100));
  };
  const handleStart = (event: ChangeEvent<HTMLInputElement>) => {
    const next = Math.min(Number(event.target.value), endValue);
    onChange({ start: dateAt(next), end: window.end });
  };
  const handleEnd = (event: ChangeEvent<HTMLInputElement>) => {
    const next = Math.max(Number(event.target.value), startValue);
    onChange({ start: window.start, end: dateAt(next) });
  };

  return (
    <div className="rounded border bg-muted/10 px-3 py-2 text-xs">
      <div className="mb-1 flex items-center justify-between text-muted-foreground">
        <span>{startDate}</span>
        <span>当前窗口：{window.start} ～ {window.end}</span>
        <button type="button" onClick={onReset} className="rounded border px-2 py-0.5 hover:bg-muted">
          双击恢复全周期
        </button>
        <span>{endDate}</span>
      </div>
      <div className="grid gap-1">
        <input aria-label="窗口开始" type="range" min={0} max={100} value={Math.max(0, Math.min(100, startValue))} onChange={handleStart} />
        <input aria-label="窗口结束" type="range" min={0} max={100} value={Math.max(0, Math.min(100, endValue))} onChange={handleEnd} />
      </div>
    </div>
  );
}
