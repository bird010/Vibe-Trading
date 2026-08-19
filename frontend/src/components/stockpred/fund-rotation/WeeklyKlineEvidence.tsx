import { ArrowDown, ArrowUp, Loader2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { TradeMarkersChart } from "./TradeMarkersChart";
import type { ChartZoomRange } from "@/components/charts/CandlestickChart";
import type { InstrumentChartResponse, InstrumentTrade } from "./types";
import type { YearlyEvidenceYear, WeeklyEvidenceEvent } from "./weeklyEvidence";

interface Props {
  years: YearlyEvidenceYear[];
  charts: Record<string, InstrumentChartResponse>;
  chartErrors: Record<string, string>;
  loading: boolean;
  onRetry: () => void;
  fullDateRange?: { start: string; end: string };
  initialEvidence?: { tsCode: string; date: string } | null;
  onFocusChange?: (tsCode: string, date: string) => void;
}

function formatValue(value: unknown): string {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function formatWeight(value: unknown): string {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(1)}%` : "—";
}

function instrumentTitle(chart: InstrumentChartResponse): string {
  return [chart.ts_code, chart.name, chart.fund_type].filter(Boolean).join(" · ");
}

function eventForInstrument(events: WeeklyEvidenceEvent[], tsCode: string): WeeklyEvidenceEvent[] {
  return events.filter((event) => event.tsCode === tsCode);
}

function eventKey(tsCode: string, date: string, index: number): string {
  return `${tsCode}:${date}:${index}`;
}

function tradeOf(event: WeeklyEvidenceEvent): InstrumentTrade | null {
  return event.kind === "signal" ? null : event.record as InstrumentTrade;
}

function evidenceRows(
  events: WeeklyEvidenceEvent[],
  tsCode: string,
  selectedDate: string | null,
  rowRefs: MutableRefObject<Record<string, HTMLTableRowElement | null>>,
  onSelect: (date: string) => void,
) {
  return events.map((event, index) => {
    const trade = tradeOf(event);
    const signal = event.kind === "signal" ? event.record : null;
    const rowKey = eventKey(tsCode, event.date, index);
    const blocked = trade ? ["BLOCKED", "REJECTED"].includes(String(trade.status ?? "").toUpperCase()) || Boolean(trade.blocked_reason) || Number(trade.filled) <= 0 : false;
    return (
      <tr
        key={rowKey}
        ref={(element) => { rowRefs.current[rowKey] = element; }}
        data-testid={`evidence-row-${tsCode}-${event.date}-${index}`}
        onClick={() => onSelect(event.date)}
        onKeyDown={(keyEvent) => {
          if (keyEvent.key === "Enter" || keyEvent.key === " ") onSelect(event.date);
        }}
        tabIndex={0}
        className={`border-b last:border-0 transition-colors ${selectedDate === event.date ? "bg-primary/15 ring-1 ring-inset ring-primary/40" : ""}`}
      >
        <td className="px-2 py-1.5 font-mono">{event.date}</td>
        <td className="px-2 py-1.5 text-center">
          {trade ? (blocked ? <X className="mx-auto h-3.5 w-3.5 text-amber-600" aria-label="阻断" /> : trade.action === "BUY" ? <ArrowUp className="mx-auto h-3.5 w-3.5 text-emerald-600" aria-label="买入" /> : <ArrowDown className="mx-auto h-3.5 w-3.5 text-red-600" aria-label="卖出" />) : "—"}
        </td>
        <td className="px-2 py-1.5">{formatWeight(signal ? signal.target_weight : trade?.target_weight)}</td>
        <td className="px-2 py-1.5">{signal ? "信号" : trade?.status === "PARTIAL" ? "部分成交" : trade?.status === "FILLED" ? "已成交" : "阻断/未成交"}</td>
        <td className="px-2 py-1.5">{formatValue(trade?.filled)}</td>
        <td className="px-2 py-1.5">{trade ? formatValue(trade.price) : "—"}</td>
        <td className="px-2 py-1.5">{formatValue(event.blockedReason ?? trade?.blocked_reason ?? trade?.reason)}</td>
      </tr>
    );
  });
}

export function WeeklyKlineEvidence({ years, charts, chartErrors, loading, onRetry, fullDateRange, initialEvidence, onFocusChange }: Props) {
  const [selectedEvidence, setSelectedEvidence] = useState<{ tsCode: string; date: string } | null>(initialEvidence ?? null);
  const [selectionVersion, setSelectionVersion] = useState(0);
  const [sharedZoomRange, setSharedZoomRange] = useState<ChartZoomRange | null>(null);
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});

  const selectEvidence = useCallback((tsCode: string, date: string) => {
    setSelectedEvidence({ tsCode, date });
    setSelectionVersion((version) => version + 1);
    onFocusChange?.(tsCode, date);
  }, [onFocusChange]);

  const handleZoomRangeChange = useCallback((next: ChartZoomRange) => {
    setSharedZoomRange((previous) => previous?.start === next.start && previous.end === next.end ? previous : next);
  }, []);

  useEffect(() => {
    if (!selectedEvidence) return;
    const rows = Object.entries(rowRefs.current).filter(([key]) => key.startsWith(`${selectedEvidence.tsCode}:${selectedEvidence.date}:`));
    rows[0]?.[1]?.scrollIntoView?.({ block: "nearest" });
  }, [selectedEvidence]);

  if (loading) {
    return <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />加载年度 K 线证据…</div>;
  }

  const errors = Object.entries(chartErrors);
  if (years.length === 0) {
    if (errors.length > 0 && Object.keys(charts).length === 0) {
      return (
        <div className="space-y-3 rounded border border-red-300 bg-red-50 px-3 py-3 text-sm text-red-700">
          <div>所有 ETF 的 K 线证据加载失败。</div>
          {errors.map(([tsCode, message]) => <div key={tsCode}>{tsCode}：{message}</div>)}
          <button type="button" onClick={onRetry} className="rounded border border-red-400 px-3 py-1">重试</button>
        </div>
      );
    }
    return <div className="rounded border bg-muted/20 px-3 py-8 text-center text-sm text-muted-foreground">当前运行没有可展示的年度 K 线证据。</div>;
  }

  return (
    <div className="space-y-6">
      {errors.length > 0 && <div className="rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800">部分 ETF 加载失败：{errors.map(([tsCode, message]) => `${tsCode}：${message}`).join("；")}</div>}
      {years.slice().sort((left, right) => left.year.localeCompare(right.year)).map((year) => (
        <section key={year.year} className="space-y-3">
          <h3 className="text-sm font-semibold">{year.year} 年 · 操作标的 {year.instruments.length} 个</h3>
          {year.instruments.map(({ tsCode, chart }) => (
            <div key={tsCode} className="space-y-4">
              <div className="grid gap-4 lg:grid-cols-2">
                <TradeMarkersChart ohlcv={chart.ohlcv} trades={chart.trades} signals={chart.signals} strategyScore={chart.strategy_evidence?.score} tsCode={tsCode} name={chart.name} fundType={chart.fund_type} dateRange={fullDateRange} height={300} focusTime={selectedEvidence?.tsCode === tsCode ? selectedEvidence.date : undefined} focusRequest={selectedEvidence?.tsCode === tsCode ? selectionVersion : undefined} sharedZoomRange={sharedZoomRange} onZoomRangeChange={handleZoomRangeChange} onMarkerClick={(date) => selectEvidence(tsCode, date)} />
                <div className="flex h-[300px] min-h-0 flex-col overflow-hidden rounded border">
                <div className="border-b bg-muted/20 px-3 py-2 text-xs">
                  {instrumentTitle(chart)} · 数据快照版本：{formatValue(chart.ohlcv_source.version)}
                  {!chart.ohlcv_source.available && chart.ohlcv_source.reason ? ` · K 线不可用：${chart.ohlcv_source.reason}` : ""}
                </div>
                <div className="min-h-0 flex-1 overflow-y-auto">
                  <table className="w-full text-xs"><thead className="sticky top-0 bg-background"><tr className="border-b text-left"><th className="px-2 py-1.5">日期</th><th className="px-2 py-1.5 text-center">买卖</th><th className="px-2 py-1.5">目标权重</th><th className="px-2 py-1.5">交易状态</th><th className="px-2 py-1.5">成交数量</th><th className="px-2 py-1.5">成交价格</th><th className="px-2 py-1.5">阻断原因</th></tr></thead><tbody>{evidenceRows(eventForInstrument(year.events, tsCode).filter((event) => event.kind !== "signal"), tsCode, selectedEvidence?.tsCode === tsCode ? selectedEvidence.date : null, rowRefs, (date) => selectEvidence(tsCode, date))}</tbody></table>
                </div>
              </div>
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}
