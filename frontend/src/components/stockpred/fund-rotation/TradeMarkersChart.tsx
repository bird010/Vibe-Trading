/** ETF candlestick chart with signal, execution and blocked-order markers. */

import { ArrowUp, ArrowDown, X } from "lucide-react";

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

const WIDTH = 760;
const HEIGHT = 320;
const LEFT = 54;
const RIGHT = 18;
const TOP = 18;
const BOTTOM = 40;
const MAX_BARS = 180;

function finite(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function dateOfSignal(signal: SignalMarker): string {
  return String(signal.date ?? signal.week_ending ?? "");
}

export function TradeMarkersChart({
  ohlcv,
  trades,
  signals = [],
  tsCode,
}: Props) {
  const bars = ohlcv
    .map((bar) => ({
      ...bar,
      open: Number(bar.open),
      high: Number(bar.high),
      low: Number(bar.low),
      close: Number(bar.close),
      vol: Number(bar.vol),
    }))
    .filter(
      (bar) =>
        bar.trade_date &&
        [bar.open, bar.high, bar.low, bar.close].every(Number.isFinite),
    )
    .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
    .slice(-MAX_BARS);

  const drawableWidth = WIDTH - LEFT - RIGHT;
  const drawableHeight = HEIGHT - TOP - BOTTOM;
  const lows = bars.map((bar) => bar.low);
  const highs = bars.map((bar) => bar.high);
  const rawMin = lows.length > 0 ? Math.min(...lows) : 0;
  const rawMax = highs.length > 0 ? Math.max(...highs) : 1;
  const padding = Math.max((rawMax - rawMin) * 0.06, rawMax * 0.002, 0.01);
  const minPrice = rawMin - padding;
  const maxPrice = rawMax + padding;
  const priceSpan = Math.max(maxPrice - minPrice, 1e-9);
  const step = bars.length > 0 ? drawableWidth / bars.length : drawableWidth;
  const candleWidth = Math.max(2, Math.min(step * 0.62, 9));
  const barIndex = new Map(bars.map((bar, index) => [bar.trade_date, index]));

  const xFor = (index: number): number => LEFT + step * (index + 0.5);
  const yFor = (price: number): number =>
    TOP + ((maxPrice - price) / priceSpan) * drawableHeight;

  const visibleTrades = trades.filter((trade) => barIndex.has(trade.trade_date));
  const visibleSignals = signals.filter((signal) => barIndex.has(dateOfSignal(signal)));
  const priceTicks = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    const price = maxPrice - priceSpan * ratio;
    return { price, y: TOP + drawableHeight * ratio };
  });

  return (
    <div className="space-y-4">
      <h4 className="text-sm font-semibold">{tsCode} · K 线与交易证据</h4>

      {bars.length === 0 ? (
        <div className="rounded border bg-muted/30 px-3 py-4 text-center text-sm text-muted-foreground">
          固定数据快照中没有可用 K 线；订单、信号和成交记录仍保留在下方。
        </div>
      ) : (
        <div className="overflow-x-auto rounded border bg-background">
          <svg
            viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
            role="img"
            aria-label={`${tsCode} candlestick chart with trade markers`}
            className="min-w-[720px] w-full h-auto"
          >
            {priceTicks.map((tick) => (
              <g key={tick.price}>
                <line
                  x1={LEFT}
                  x2={WIDTH - RIGHT}
                  y1={tick.y}
                  y2={tick.y}
                  className="stroke-border"
                  strokeWidth={0.7}
                />
                <text
                  x={LEFT - 6}
                  y={tick.y + 3}
                  textAnchor="end"
                  className="fill-muted-foreground text-[9px]"
                >
                  {tick.price.toFixed(2)}
                </text>
              </g>
            ))}

            {bars.map((bar, index) => {
              const x = xFor(index);
              const openY = yFor(bar.open);
              const closeY = yFor(bar.close);
              const highY = yFor(bar.high);
              const lowY = yFor(bar.low);
              const rising = bar.close >= bar.open;
              const bodyY = Math.min(openY, closeY);
              const bodyHeight = Math.max(Math.abs(openY - closeY), 1.2);
              return (
                <g key={`${bar.trade_date}-${index}`}>
                  <line
                    x1={x}
                    x2={x}
                    y1={highY}
                    y2={lowY}
                    className={rising ? "stroke-emerald-600" : "stroke-red-500"}
                    strokeWidth={1}
                  />
                  <rect
                    x={x - candleWidth / 2}
                    y={bodyY}
                    width={candleWidth}
                    height={bodyHeight}
                    className={rising ? "fill-emerald-500" : "fill-red-500"}
                    opacity={0.82}
                  />
                  <title>
                    {`${bar.trade_date} O ${bar.open.toFixed(2)} H ${bar.high.toFixed(2)} L ${bar.low.toFixed(2)} C ${bar.close.toFixed(2)}`}
                  </title>
                </g>
              );
            })}

            {visibleSignals.map((signal, index) => {
              const date = dateOfSignal(signal);
              const barPosition = barIndex.get(date)!;
              const x = xFor(barPosition);
              const y = TOP + 10 + (index % 3) * 7;
              return (
                <g key={`signal-${date}-${index}`}>
                  <circle
                    cx={x}
                    cy={y}
                    r={3.2}
                    className="fill-blue-500"
                  />
                  <title>{`${date} 目标权重 ${(signal.target_weight * 100).toFixed(2)}%`}</title>
                </g>
              );
            })}

            {visibleTrades.map((trade, index) => {
              const barPosition = barIndex.get(trade.trade_date)!;
              const x = xFor(barPosition);
              const price = finite(trade.price) ?? bars[barPosition].close;
              const blocked = Boolean(
                trade.blocked_reason ||
                  trade.reason ||
                  trade.status === "BLOCKED" ||
                  Number(trade.filled) <= 0,
              );
              const y = blocked
                ? yFor(bars[barPosition].high) - 9
                : trade.action === "BUY"
                  ? yFor(price) + 11
                  : yFor(price) - 11;
              const points =
                trade.action === "BUY"
                  ? `${x},${y - 6} ${x - 5},${y + 3} ${x + 5},${y + 3}`
                  : `${x},${y + 6} ${x - 5},${y - 3} ${x + 5},${y - 3}`;
              return blocked ? (
                <g key={`trade-${trade.trade_date}-${index}`}>
                  <line
                    x1={x - 4}
                    y1={y - 4}
                    x2={x + 4}
                    y2={y + 4}
                    className="stroke-amber-600"
                    strokeWidth={2}
                  />
                  <line
                    x1={x + 4}
                    y1={y - 4}
                    x2={x - 4}
                    y2={y + 4}
                    className="stroke-amber-600"
                    strokeWidth={2}
                  />
                  <title>{`${trade.trade_date} 阻断：${trade.blocked_reason ?? trade.reason ?? trade.status ?? "未成交"}`}</title>
                </g>
              ) : (
                <polygon
                  key={`trade-${trade.trade_date}-${index}`}
                  points={points}
                  className={
                    trade.action === "BUY" ? "fill-emerald-700" : "fill-red-700"
                  }
                >
                  <title>{`${trade.trade_date} ${trade.action} ${trade.filled} @ ${price.toFixed(3)}`}</title>
                </polygon>
              );
            })}

            {bars.length > 0 && (
              <>
                <text
                  x={LEFT}
                  y={HEIGHT - 12}
                  className="fill-muted-foreground text-[9px]"
                >
                  {bars[0].trade_date}
                </text>
                <text
                  x={WIDTH - RIGHT}
                  y={HEIGHT - 12}
                  textAnchor="end"
                  className="fill-muted-foreground text-[9px]"
                >
                  {bars[bars.length - 1].trade_date}
                </text>
              </>
            )}
          </svg>
          <div className="flex flex-wrap gap-4 border-t px-3 py-2 text-xs text-muted-foreground">
            <span>蓝点：目标权重信号</span>
            <span>绿色三角：买入成交</span>
            <span>红色三角：卖出成交</span>
            <span>橙色叉：阻断或未成交</span>
          </div>
        </div>
      )}

      {signals.length > 0 && (
        <div className="space-y-1">
          <h5 className="text-xs font-medium text-muted-foreground">信号记录</h5>
          <div className="max-h-40 overflow-y-auto space-y-0.5">
            {signals.map((signal, index) => (
              <div
                key={`${dateOfSignal(signal)}-${index}`}
                className="text-xs flex justify-between border-b py-0.5"
              >
                <span className="font-mono">{dateOfSignal(signal)}</span>
                <span>目标权重：{(signal.target_weight * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {trades.length === 0 && signals.length === 0 && (
        <div className="text-xs text-muted-foreground">暂无信号或交易记录。</div>
      )}

      {trades.length > 0 && (
        <div className="space-y-1">
          <h5 className="text-xs font-medium text-muted-foreground">成交与阻断记录</h5>
          <div className="max-h-96 overflow-y-auto space-y-0.5">
            {trades.map((trade, index) => {
              const blockedReason = trade.blocked_reason ?? trade.reason;
              const blocked = Boolean(
                blockedReason ||
                  trade.status === "BLOCKED" ||
                  Number(trade.filled) <= 0,
              );
              const amount =
                finite(trade.amount) ?? Number(trade.filled) * Number(trade.price);
              return (
                <div
                  key={`${trade.trade_date}-${trade.action}-${index}`}
                  className={`text-xs flex items-center gap-2 border-b py-1 ${
                    blocked ? "opacity-60" : ""
                  }`}
                >
                  {blocked ? (
                    <X className="h-3 w-3 text-amber-600 shrink-0" />
                  ) : trade.action === "BUY" ? (
                    <ArrowUp className="h-3 w-3 text-emerald-600 shrink-0" />
                  ) : (
                    <ArrowDown className="h-3 w-3 text-red-600 shrink-0" />
                  )}
                  <span className="font-mono shrink-0 w-20">{trade.trade_date}</span>
                  <span className="shrink-0 w-14">{trade.action}</span>
                  <span className="shrink-0 w-16 text-right">{trade.filled}</span>
                  <span className="shrink-0 w-20 text-right font-mono">
                    @{Number(trade.price || 0).toFixed(3)}
                  </span>
                  <span className="shrink-0 w-24 text-right font-mono">
                    ¥{Number(amount || 0).toFixed(0)}
                  </span>
                  {blocked && (
                    <span className="text-amber-700 truncate" title={blockedReason}>
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
