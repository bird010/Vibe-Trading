/** Phase 5 Task 6 — ETF trade timing and quantity markers (§15.1). */

import { ArrowUp, ArrowDown, X } from "lucide-react";

export interface TradeMarker {
  trade_date: string;
  ts_code: string;
  name?: string;
  action: "BUY" | "SELL";
  filled: number;
  price: number;
  amount: number;
  fee?: number;
  signal_date?: string;
  target_weight?: number;
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

interface Props {
  ohlcv: OHLCVBar[];
  trades: TradeMarker[];
  signals?: Array<{ date: string; target_weight: number }>;
  tsCode: string;
}

export function TradeMarkersChart({ ohlcv, trades, signals, tsCode }: Props) {
  const hasData = ohlcv.length > 0;

  return (
    <div className="space-y-4">
      <h4 className="text-sm font-semibold flex items-center gap-2">
        {tsCode} · 交易记录
      </h4>

      {!hasData && (
        <div className="rounded border bg-muted/30 px-3 py-4 text-center text-sm text-muted-foreground">
          无可用 K 线数据 — 仅展示订单记录
        </div>
      )}

      {/* Updated plan — chart rendering deferred to chart integration phase;
          this component provides the data view for now. */}
      {trades.length === 0 && signals && signals.length === 0 && (
        <div className="text-xs text-muted-foreground">暂无交易</div>
      )}

      {signals && signals.length > 0 && (
        <div className="space-y-1">
          <h5 className="text-xs font-medium text-muted-foreground">信号记录</h5>
          <div className="max-h-40 overflow-y-auto space-y-0.5">
            {signals.map((s, i) => (
              <div key={i} className="text-xs flex justify-between border-b py-0.5">
                <span className="font-mono">{s.date}</span>
                <span>目标权重：{(s.target_weight * 100).toFixed(1)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {trades.length > 0 && (
        <div className="space-y-1">
          <h5 className="text-xs font-medium text-muted-foreground">成交记录</h5>
          <div className="max-h-96 overflow-y-auto space-y-0.5">
            {trades.map((t, i) => (
              <div
                key={i}
                className={`text-xs flex items-center gap-2 border-b py-1 ${
                  t.blocked_reason ? "opacity-50" : ""
                }`}
              >
                {t.action === "BUY" ? (
                  <ArrowUp className="h-3 w-3 text-green-500 shrink-0" />
                ) : t.blocked_reason ? (
                  <X className="h-3 w-3 text-red-400 shrink-0" />
                ) : (
                  <ArrowDown className="h-3 w-3 text-red-500 shrink-0" />
                )}
                <span className="font-mono shrink-0 w-20">{t.trade_date}</span>
                <span className="shrink-0 w-8 text-right">{t.filled}</span>
                <span className="shrink-0 w-20 text-right font-mono">
                  @{t.price.toFixed(2)}
                </span>
                <span className="shrink-0 w-24 text-right font-mono">
                  ¥{(t.amount ?? t.filled * t.price).toFixed(0)}
                </span>
                {t.blocked_reason && (
                  <span className="text-red-500 truncate" title={t.blocked_reason}>
                    {t.blocked_reason}
                  </span>
                )}
                {t.signal_date && (
                  <span className="text-muted-foreground">
                    信号: {t.signal_date}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
