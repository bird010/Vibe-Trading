/** Fund-rotation evidence adapter backed by the shared ECharts candlestick. */

import { useMemo } from "react";
import { CandlestickChart, type ChartZoomRange } from "@/components/charts/CandlestickChart";
import type {
  PriceBar,
  TradeMarker as SharedTradeMarker,
} from "@/lib/api";
import type { StrategyEvidenceSeries, StrategyScoreEvidence } from "./types";

export interface TradeMarker {
  trade_date: string;
  ts_code?: string;
  code?: string;
  name?: string;
  action: "BUY" | "SELL";
  status?: string;
  filled: number;
  price: number;
  amount?: number;
  commission?: number;
  fee?: number;
  signal_date?: string;
  target_weight?: number;
  reason?: string;
  blocked_reason?: string;
}

export interface OHLCVBar {
  trade_date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  vol: number;
}

export interface SignalMarker {
  date?: string;
  week_ending?: string;
  target_weight: number;
}

interface Props {
  ohlcv: OHLCVBar[];
  trades: TradeMarker[];
  signals?: SignalMarker[];
  tsCode: string;
  name?: string | null;
  fundType?: string | null;
  focusTime?: string;
  focusRequest?: number;
  sharedZoomRange?: ChartZoomRange | null;
  onZoomRangeChange?: (range: ChartZoomRange) => void;
  onMarkerClick?: (time: string) => void;
  dateRange?: { start: string; end: string };
  strategyScore?: StrategyScoreEvidence | null;
  /** @deprecated V2 compatibility for old artifacts. */
  strategyIndicators?: StrategyEvidenceSeries[];
  height?: number;
}

const EMPTY_SIGNALS: SignalMarker[] = [];

function finite(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function canonicalDate(value: unknown): string {
  const text = String(value ?? "").trim();
  const withoutDecimal = /^\d+\.0$/.test(text) ? text.slice(0, -2) : text;
  const digits = withoutDecimal.replace(/\D/g, "");
  return digits.length >= 8 ? digits.slice(0, 8) : withoutDecimal;
}

function dateOfSignal(signal: SignalMarker): string {
  return canonicalDate(signal.date ?? signal.week_ending ?? "");
}

function scoreFrequencyLabel(frequency: string | undefined): string {
  const labels: Record<string, string> = {
    DAILY: "日频",
    WEEKLY: "周频",
    MONTHLY: "月频",
  };
  return labels[String(frequency ?? "").toUpperCase()] ?? frequency ?? "";
}

function markerReason(trade: TradeMarker): string | undefined {
  const details: string[] = [];
  const reason = trade.blocked_reason ?? trade.reason;
  if (reason) details.push(reason);
  if (trade.signal_date) {
    details.push(`Signal ${canonicalDate(trade.signal_date)}`);
  }
  const targetWeight = finite(trade.target_weight);
  if (targetWeight !== null) {
    details.push(`Target ${(targetWeight * 100).toFixed(2)}%`);
  }
  return details.length > 0 ? details.join(" · ") : undefined;
}

export function TradeMarkersChart({
  ohlcv,
  trades,
  signals,
  tsCode,
  name,
  fundType,
  focusTime,
  focusRequest,
  sharedZoomRange,
  onZoomRangeChange,
  onMarkerClick,
  dateRange,
  strategyScore,
  strategyIndicators,
  height = 500,
}: Props) {
  const rangeStart = dateRange ? canonicalDate(dateRange.start) : null;
  const rangeEnd = dateRange ? canonicalDate(dateRange.end) : null;
  const {
    filteredSignals,
    filteredTrades,
    priceBars,
    chartMarkers,
    strategyOverlay,
  } = useMemo(() => {
    const inRange = (date: string): boolean =>
      !rangeStart || !rangeEnd || (date >= rangeStart && date <= rangeEnd);
    const normalizedSignals = (signals ?? EMPTY_SIGNALS).map((signal) => ({
      ...signal,
      date: signal.date ? canonicalDate(signal.date) : signal.date,
      week_ending: signal.week_ending
        ? canonicalDate(signal.week_ending)
        : signal.week_ending,
    }));
    const normalizedTrades = trades.map((trade) => ({
      ...trade,
      trade_date: canonicalDate(trade.trade_date),
      signal_date: trade.signal_date
        ? canonicalDate(trade.signal_date)
        : trade.signal_date,
    }));
    const filteredSignals = normalizedSignals.filter((signal) =>
      inRange(dateOfSignal(signal)),
    );
    const filteredTrades = normalizedTrades.filter((trade) =>
      inRange(trade.trade_date),
    );

    const priceBars: PriceBar[] = ohlcv
      .map((bar) => ({
        time: canonicalDate(bar.trade_date),
        code: tsCode,
        open: Number(bar.open),
        high: Number(bar.high),
        low: Number(bar.low),
        close: Number(bar.close),
        volume: finite(bar.vol) ?? 0,
      }))
      .filter(
        (bar) =>
          bar.time &&
          inRange(bar.time) &&
          [bar.open, bar.high, bar.low, bar.close].every(Number.isFinite),
      )
      .sort((left, right) => left.time.localeCompare(right.time));

    const closeByDate = new Map(
      priceBars.map((bar) => [bar.time, bar.close]),
    );
    const chartMarkers: SharedTradeMarker[] = filteredTrades
      .filter((trade) => closeByDate.has(trade.trade_date))
      .map((trade) => {
        const status = String(trade.status ?? "").toUpperCase();
        const filled = finite(trade.filled) ?? 0;
        const blocked = Boolean(
          trade.blocked_reason ||
            status === "BLOCKED" ||
            status === "REJECTED" ||
            filled <= 0,
        );
        const price = finite(trade.price);
        return {
          time: trade.trade_date,
          code: trade.ts_code ?? trade.code ?? tsCode,
          side: trade.action,
          price:
            price !== null && price > 0
              ? price
              : closeByDate.get(trade.trade_date) ?? 0,
          qty: Math.abs(filled),
          status: blocked
            ? "REJECTED"
            : status === "PARTIAL"
              ? "PARTIAL"
              : "FILLED",
          reason: markerReason(trade),
        };
      });
    const strategyOverlay = Object.fromEntries(
      strategyScore
        ? [[strategyScore.label || `策略得分（${scoreFrequencyLabel(strategyScore.frequency)}）`, strategyScore.points
            .map((point) => ({ time: canonicalDate(point.date), value: Number(point.value) }))
            .filter((point) => point.time && inRange(point.time) && Number.isFinite(point.value))]]
        : (strategyIndicators ?? []).map((indicator) => [
            indicator.label,
            indicator.points
              .map((point) => ({ time: canonicalDate(point.date), value: Number(point.value) }))
              .filter((point) => point.time && inRange(point.time) && Number.isFinite(point.value)),
          ]),
    );
    return { filteredSignals, filteredTrades, priceBars, chartMarkers, strategyOverlay };
  }, [ohlcv, trades, signals, strategyScore, strategyIndicators, tsCode, rangeStart, rangeEnd]);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold">{[tsCode, name, fundType].filter(Boolean).join(" · ")} · K 线与交易证据</h4>
        {(filteredTrades.length > 0 || filteredSignals.length > 0) && (
          <span className="text-xs text-muted-foreground">
            图中显示 {chartMarkers.length}/{filteredTrades.length} 笔成交或阻断
          </span>
        )}
      </div>

      {priceBars.length === 0 ? (
        <div className="rounded border bg-muted/30 px-3 py-4 text-center text-sm text-muted-foreground">
          固定数据快照中没有可用 K 线；订单、信号和成交记录仍保留在下方。
        </div>
      ) : (
        <div className="rounded border bg-background p-2">
          <CandlestickChart
            data={priceBars}
            markers={chartMarkers}
            height={height}
            focusTime={focusTime}
            focusRequest={focusRequest}
            sharedZoomRange={sharedZoomRange}
            onZoomRangeChange={onZoomRangeChange}
            onMarkerClick={onMarkerClick}
            strategyScore={strategyOverlay}
          />
        </div>
      )}
    </div>
  );
}
