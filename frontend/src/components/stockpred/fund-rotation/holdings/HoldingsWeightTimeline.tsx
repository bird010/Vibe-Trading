import { useMemo, useState } from "react";
import type { HoldingInterval, HoldingsTimelineResponse } from "../types";
import { HoldingTooltip } from "./HoldingTooltip";
import { HoldingsTimeBrush } from "./HoldingsTimeBrush";
import { dateKeyAtTimestamp, dateTimestamp } from "./dateUtils";

interface Props {
  data: HoldingsTimelineResponse;
  selectedSignalDate: string | null;
  window: { start: string; end: string } | null;
  onWindowChange: (window: { start: string; end: string }) => void;
  onSelectSignalDate: (signalDate: string) => void;
}

function displayName(data: HoldingsTimelineResponse, code: string): string {
  if (code === "_CASH") return "Cash";
  return data.instruments.find((item) => item.ts_code === code)?.name || code;
}

function zoomLabel(start: string, end: string): string {
  const days = Math.max(0, (dateTimestamp(end) - dateTimestamp(start)) / 86400000);
  const duration = Math.abs(days);
  return duration > 1095 ? "年/半年" : duration > 183 ? "月" : "周/日";
}

function intervalKey(interval: HoldingInterval): string {
  return `${interval.ts_code}-${interval.start_date}-${interval.end_date}`;
}

interface TimelineTick {
  timestamp: number;
  label: string;
}

function calendarTicks(startDate: string, endDate: string): TimelineTick[] {
  const start = dateTimestamp(startDate);
  const end = dateTimestamp(endDate);
  const days = Math.max(0, (end - start) / 86400000);
  const startKey = dateKeyAtTimestamp(start);
  const endKey = dateKeyAtTimestamp(end);
  const ticks: TimelineTick[] = [
    { timestamp: start, label: `${startKey.slice(0, 4)}-${startKey.slice(4, 6)}` },
  ];
  if (days > 183) {
    const startValue = new Date(start);
    const stepMonths = days > 1095 ? 12 : 3;
    let cursor = Date.UTC(startValue.getUTCFullYear(), startValue.getUTCMonth() + stepMonths, 1);
    while (cursor < end) {
      const date = new Date(cursor);
      const label = days > 1095
        ? String(date.getUTCFullYear())
        : `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}`;
      ticks.push({ timestamp: cursor, label });
      cursor = Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + stepMonths, 1);
    }
  } else {
    const stepDays = days > 60 ? 14 : 7;
    for (let cursor = start + stepDays * 86400000; cursor < end; cursor += stepDays * 86400000) {
      const key = dateKeyAtTimestamp(cursor);
      ticks.push({ timestamp: cursor, label: `${key.slice(4, 6)}-${key.slice(6, 8)}` });
    }
  }
  const endValue = new Date(end);
  ticks.push({
    timestamp: end,
    label: days > 183
      ? `${endValue.getUTCFullYear()}-${String(endValue.getUTCMonth() + 1).padStart(2, "0")}`
      : `${endKey.slice(4, 6)}-${endKey.slice(6, 8)}`,
  });
  return ticks.filter((tick, index) => index === 0 || tick.timestamp !== ticks[index - 1].timestamp);
}

export function HoldingsWeightTimeline({ data, selectedSignalDate, window, onWindowChange, onSelectSignalDate }: Props) {
  const [showAllRows, setShowAllRows] = useState(false);
  const [hoveredIntervalKey, setHoveredIntervalKey] = useState<string | null>(null);
  const currentWindow = window ?? { start: data.start_date ?? "", end: data.end_date ?? "" };
  const start = dateTimestamp(currentWindow.start);
  const end = Math.max(dateTimestamp(currentWindow.end), start + 86400000);
  const width = 960;
  const rowHeight = 36;
  const rows = useMemo(() => {
    const codes = Array.from(new Set(data.intervals.map((interval) => interval.ts_code)));
    const total = (code: string) => data.intervals
      .filter((interval) => interval.ts_code === code)
      .reduce((sum, interval) => {
        const overlapStart = Math.max(dateTimestamp(interval.start_date), dateTimestamp(currentWindow.start));
        const overlapEnd = Math.min(dateTimestamp(interval.end_date), dateTimestamp(currentWindow.end));
        const overlapDays = overlapEnd >= overlapStart ? Math.max(86400000, overlapEnd - overlapStart) : 0;
        return sum + overlapDays * interval.actual_weight;
      }, 0);
    return codes.filter((code) => total(code) > 0).sort((left, right) => {
      return total(right) - total(left);
    });
  }, [currentWindow.end, currentWindow.start, data.intervals]);
  const days = Math.abs((dateTimestamp(currentWindow.end) - dateTimestamp(currentWindow.start)) / 86400000);
  const visibleRows = days > 1095 && !showAllRows ? rows.slice(0, 12) : rows;
  const hiddenRowCount = Math.max(0, rows.length - visibleRows.length);
  const visibleMarkers = useMemo(() => {
    if (days <= 183) return data.rebalance_markers;
    if (days <= 1095) return data.rebalance_markers.filter((marker) => (marker.actual_changed_positions ?? marker.changed_positions) > 0);
    return data.rebalance_markers.filter((marker) => {
      const changed = marker.actual_changed_positions ?? marker.changed_positions;
      return changed > 1 || (marker.execution_turnover ?? marker.turnover ?? 0) >= 0.25 || (marker.cash_target_weight ?? 0) > 0 || Boolean(marker.quality_status && marker.quality_status.toUpperCase() !== "VALID");
    });
  }, [data.rebalance_markers, days]);
  const height = Math.max(130, visibleRows.length * rowHeight + 42);
  const xAt = (value: number) => ((value - start) / (end - start)) * (width - 180) + 160;
  const ticks = calendarTicks(currentWindow.start, currentWindow.end);
  const clipped = (interval: HoldingInterval) => {
    const left = Math.max(dateTimestamp(interval.start_date), start);
    const right = Math.min(dateTimestamp(interval.end_date), end);
    return right >= left ? { left, right } : null;
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>实际 ETF 持仓 · 连续区间</span>
        <span>时间刻度：{zoomLabel(currentWindow.start, currentWindow.end)}</span>
      </div>
      <div className="overflow-x-auto rounded border bg-background">
        <svg viewBox={`0 0 ${width} ${height}`} className="min-w-[720px] w-full" role="img" aria-label="持仓与权重变化时间线">
          <line x1="160" x2={width - 20} y1="26" y2="26" stroke="currentColor" opacity="0.2" />
          {ticks.map((tick, index) => (
            <g key={`${tick.timestamp}-${tick.label}`}>
              <line x1={xAt(tick.timestamp)} x2={xAt(tick.timestamp)} y1="23" y2="29" stroke="currentColor" opacity="0.35" />
              <text x={xAt(tick.timestamp)} y="17" textAnchor={index === 0 ? "start" : index === ticks.length - 1 ? "end" : "middle"} className="fill-current text-[10px]">{tick.label}</text>
            </g>
          ))}
          {visibleRows.map((code, rowIndex) => (
            <g key={code}>
              <text x="8" y={rowIndex * rowHeight + 51} className="fill-current text-[11px]">{displayName(data, code)}</text>
              {data.intervals.filter((interval) => interval.ts_code === code).map((interval) => {
                const bounds = clipped(interval);
                if (!bounds) return null;
                const x = xAt(bounds.left);
                const right = xAt(bounds.right);
                const barWidth = Math.max(3, right - x);
                const y = rowIndex * rowHeight + 34;
                return (
                  <g key={intervalKey(interval)}>
                    <rect
                      data-testid={`holding-interval-${intervalKey(interval)}`}
                      x={x}
                      y={y}
                      width={barWidth}
                      height="18"
                      rx="3"
                      fill={`rgba(37, 99, 235, ${Math.min(0.95, 0.2 + interval.actual_weight * 0.8)})`}
                      aria-label={`${displayName(data, code)} ${interval.start_date} 至 ${interval.end_date} 实际权重 ${(interval.actual_weight * 100).toFixed(2)}%`}
                      onMouseEnter={() => setHoveredIntervalKey(intervalKey(interval))}
                      onMouseLeave={() => setHoveredIntervalKey(null)}
                    >
                      <title>{`${displayName(data, code)} ${interval.start_date} ～ ${interval.end_date} 实际权重 ${(interval.actual_weight * 100).toFixed(2)}%`}</title>
                    </rect>
                    {barWidth > 70 && <text x={x + 5} y={y + 13} className="fill-white text-[10px]">{(interval.actual_weight * 100).toFixed(1)}%</text>}
                  </g>
                );
              })}
            </g>
          ))}
          {visibleMarkers.map((marker) => {
            const x = xAt(dateTimestamp(marker.signal_date));
            return (
              <g
                key={marker.decision_id}
                data-testid={`holding-marker-${marker.signal_date}`}
                role="button"
                tabIndex={0}
                onClick={() => onSelectSignalDate(marker.signal_date)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") onSelectSignalDate(marker.signal_date);
                }}
                className={selectedSignalDate === marker.signal_date ? "text-blue-700" : "text-muted-foreground"}
              >
                <line x1={x} x2={x} y1="26" y2={height - 12} stroke="currentColor" strokeDasharray="3 3" opacity="0.7" />
                <circle cx={x} cy="26" r="4" fill="currentColor" />
                <title>{`${marker.signal_date} 调仓，变化 ${marker.changed_positions} 个持仓`}</title>
              </g>
            );
          })}
        </svg>
      </div>
      {hiddenRowCount > 0 && <button type="button" className="text-xs text-blue-700 underline" onClick={() => setShowAllRows(true)}>其他持仓 {hiddenRowCount} 项</button>}
      <HoldingsTimeBrush
        startDate={data.start_date ?? currentWindow.start}
        endDate={data.end_date ?? currentWindow.end}
        window={currentWindow}
        onChange={onWindowChange}
        onReset={() => onWindowChange({ start: data.start_date ?? currentWindow.start, end: data.end_date ?? currentWindow.end })}
      />
      {hoveredIntervalKey && (() => {
        const hovered = data.intervals.find((interval) => intervalKey(interval) === hoveredIntervalKey);
        return hovered ? <div role="tooltip" className="rounded border bg-background px-2 py-1 text-xs shadow-sm"><HoldingTooltip interval={hovered} /></div> : null;
      })()}
    </div>
  );
}
