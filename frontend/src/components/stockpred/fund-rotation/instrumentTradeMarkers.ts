import type { InstrumentTrade } from "./types";

export type InstrumentTradeStatus = "FILLED" | "PARTIAL" | "REJECTED";

function finite(value: unknown): number | null {
  if (
    value === null ||
    value === undefined ||
    typeof value === "boolean" ||
    (typeof value === "string" && value.trim() === "")
  ) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function instrumentTradeStatus(
  trade: InstrumentTrade,
): InstrumentTradeStatus {
  const status = String(trade.status ?? "").toUpperCase();
  const filled = finite(trade.filled) ?? 0;
  if (
    trade.blocked_reason ||
    status === "BLOCKED" ||
    status === "REJECTED" ||
    filled <= 0
  ) {
    return "REJECTED";
  }
  return status === "PARTIAL" ? "PARTIAL" : "FILLED";
}

export function instrumentTradeExitDelayDays(
  trade: InstrumentTrade,
): number | null {
  const days = finite(trade.exit_delay_days);
  return days !== null && days > 0 ? days : null;
}
