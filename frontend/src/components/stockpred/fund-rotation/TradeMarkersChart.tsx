/** Fund-rotation evidence adapter backed by the shared ECharts candlestick. */

import { ArrowDown, ArrowUp, X } from "lucide-react";
import { CandlestickChart } from "@/components/charts/CandlestickChart";
import type {
  PriceBar,
  TradeMarker as SharedTradeMarker,
} from "@/lib/api";

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
}

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
  signals = [],
  tsCode,
}: Props) {
  const normalizedSignals = signals.map((signal) => ({
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
        [bar.open, bar.high, bar.low, bar.close].every(Number.isFinite),
    )
    .sort((left, right) => left.time.localeCompare(right.time));

  const closeByDate = new Map(
    priceBars.map((bar) => [bar.time, bar.close]),
  );
  const chartMarkers: SharedTradeMarker[] = normalizedTrades
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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold">{tsCode} · K 线与交易证据</h4>
        {(normalizedTrades.length > 0 || normalizedSignals.length > 0) && (
          <span className="text-xs text-muted-foreground">
            图中显示 {chartMarkers.length}/{normalizedTrades.length} 笔成交或阻断；
            下方记录 {normalizedSignals.length} 条目标权重信号
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
            height={500}
          />
          <div className="flex flex-wrap gap-4 border-t px-2 py-2 text-xs text-muted-foreground">
            <span>B：买入成交</span>
            <span>S：卖出成交</span>
            <span>P：部分成交</span>
            <span>X：阻断或未成交</span>
            <span>目标权重信号保留在下方证据列表</span>
          </div>
        </div>
      )}

      {normalizedSignals.length > 0 && (
        <div className="space-y-1">
          <h5 className="text-xs font-medium text-muted-foreground">信号记录</h5>
          <div className="max-h-40 space-y-0.5 overflow-y-auto">
            {normalizedSignals.map((signal, index) => (
              <div
                key={`${dateOfSignal(signal)}-${index}`}
                className="flex justify-between border-b py-0.5 text-xs"
              >
                <span className="font-mono">{dateOfSignal(signal)}</span>
                <span>目标权重：{(signal.target_weight * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {normalizedTrades.length === 0 && normalizedSignals.length === 0 && (
        <div className="text-xs text-muted-foreground">暂无信号或交易记录。</div>
      )}

      {normalizedTrades.length > 0 && (
        <div className="space-y-1">
          <h5 className="text-xs font-medium text-muted-foreground">成交与阻断记录</h5>
          <div className="max-h-96 space-y-0.5 overflow-y-auto">
            {normalizedTrades.map((trade, index) => {
              const status = String(trade.status ?? "").toUpperCase();
              const blocked = Boolean(
                trade.blocked_reason ||
                  status === "BLOCKED" ||
                  status === "REJECTED" ||
                  Number(trade.filled) <= 0,
              );
              const blockedReason = blocked
                ? trade.blocked_reason ?? trade.reason
                : undefined;
              const amount =
                finite(trade.amount) ?? Number(trade.filled) * Number(trade.price);
              return (
                <div
                  key={`${trade.trade_date}-${trade.action}-${index}`}
                  className={`flex items-center gap-2 border-b py-1 text-xs ${
                    blocked ? "opacity-60" : ""
                  }`}
                >
                  {blocked ? (
                    <X className="h-3 w-3 shrink-0 text-amber-600" />
                  ) : trade.action === "BUY" ? (
                    <ArrowUp className="h-3 w-3 shrink-0 text-emerald-600" />
                  ) : (
                    <ArrowDown className="h-3 w-3 shrink-0 text-red-600" />
                  )}
                  <span className="w-20 shrink-0 font-mono">{trade.trade_date}</span>
                  <span className="w-14 shrink-0">{trade.action}</span>
                  <span className="w-16 shrink-0 text-right">{trade.filled}</span>
                  <span className="w-20 shrink-0 text-right font-mono">
                    @{Number(trade.price || 0).toFixed(3)}
                  </span>
                  <span className="w-24 shrink-0 text-right font-mono">
                    ¥{Number(amount || 0).toFixed(0)}
                  </span>
                  {blocked && (
                    <span className="truncate text-amber-700" title={blockedReason}>
                      {blockedReason ?? trade.status ?? "未成交"}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
