import type { BacktestDetailTab } from "./types";

export interface FundRotationUrlState {
  runId: string | null;
  tab: BacktestDetailTab | null;
  signalDate: string | null;
  instrument: string | null;
  focusDate: string | null;
  strategyScore: string | null;
}

const TAB_VALUES = new Set<BacktestDetailTab>([
  "overview",
  "equity",
  "rotation_analysis",
  "chart",
  "candidate_pool",
]);

function valueOrNull(value: string | null): string | null {
  const trimmed = value?.trim() ?? "";
  return trimmed || null;
}

export function readFundRotationUrl(input: string | URL = window.location.href): FundRotationUrlState {
  const url = new URL(input.toString(), window.location.origin);
  const tabValue = valueOrNull(url.searchParams.get("detail_tab"));
  return {
    runId: valueOrNull(url.searchParams.get("run_id")),
    tab: tabValue && TAB_VALUES.has(tabValue as BacktestDetailTab) ? tabValue as BacktestDetailTab : null,
    signalDate: valueOrNull(url.searchParams.get("signal_date")),
    instrument: valueOrNull(url.searchParams.get("instrument")),
    focusDate: valueOrNull(url.searchParams.get("focus_date")),
    strategyScore: valueOrNull(url.searchParams.get("strategy_score") ?? url.searchParams.get("strategy_indicator")),
  };
}

export function updateFundRotationUrl(
  input: string | URL,
  patch: Partial<FundRotationUrlState>,
): string {
  const url = new URL(input.toString(), window.location.origin);
  const keys: Array<[keyof FundRotationUrlState, string]> = [
    ["runId", "run_id"],
    ["tab", "detail_tab"],
    ["signalDate", "signal_date"],
    ["instrument", "instrument"],
    ["focusDate", "focus_date"],
    ["strategyScore", "strategy_score"],
  ];
  for (const [field, key] of keys) {
    if (!(field in patch)) continue;
    const value = patch[field];
    if (value) url.searchParams.set(key, value);
    else url.searchParams.delete(key);
  }
  return url.toString();
}

export function syncFundRotationUrl(
  patch: Partial<FundRotationUrlState>,
  mode: "push" | "replace" = "replace",
): void {
  if (typeof window === "undefined") return;
  const next = updateFundRotationUrl(window.location.href, patch);
  window.history[mode === "push" ? "pushState" : "replaceState"]({}, "", next);
}
