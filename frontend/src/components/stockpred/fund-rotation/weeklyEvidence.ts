import type {
  InstrumentChartResponse,
  InstrumentSignal,
  InstrumentTrade,
} from "./types";

export interface WeeklyEvidenceEvent {
  kind: "signal" | "trade" | "blocked";
  date: string;
  tsCode: string;
  record: InstrumentSignal | InstrumentTrade;
  blockedReason?: string;
}

export interface WeeklyEvidenceInstrument {
  tsCode: string;
  chart: InstrumentChartResponse;
  coreDates: { start: string; end: string };
}

export interface WeeklyEvidenceWeek {
  weekStart: string;
  weekEnd: string;
  instruments: WeeklyEvidenceInstrument[];
  events: WeeklyEvidenceEvent[];
}

export interface YearlyEvidenceInstrument {
  tsCode: string;
  chart: InstrumentChartResponse;
}

export interface YearlyEvidenceYear {
  year: string;
  instruments: YearlyEvidenceInstrument[];
  events: WeeklyEvidenceEvent[];
}

function dateParts(value: string): [number, number, number] | null {
  const normalized = normalizeEvidenceDate(value);
  if (!normalized) return null;
  return [
    Number(normalized.slice(0, 4)),
    Number(normalized.slice(4, 6)),
    Number(normalized.slice(6, 8)),
  ];
}

function formatUtcDate(value: Date): string {
  return [
    value.getUTCFullYear(),
    String(value.getUTCMonth() + 1).padStart(2, "0"),
    String(value.getUTCDate()).padStart(2, "0"),
  ].join("");
}

export function normalizeEvidenceDate(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  const digits = String(value).match(/\d/g)?.join("").slice(0, 8) ?? "";
  if (digits.length !== 8) return null;

  const year = Number(digits.slice(0, 4));
  const month = Number(digits.slice(4, 6));
  const day = Number(digits.slice(6, 8));
  const date = new Date(Date.UTC(year, month - 1, day));
  if (
    date.getUTCFullYear() !== year ||
    date.getUTCMonth() !== month - 1 ||
    date.getUTCDate() !== day
  ) {
    return null;
  }
  return digits;
}

export function weekKeyForDate(date: string): string {
  const parts = dateParts(date);
  if (!parts) throw new Error(`Invalid evidence date: ${date}`);
  const current = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  const daysSinceMonday = (current.getUTCDay() + 6) % 7;
  current.setUTCDate(current.getUTCDate() - daysSinceMonday);
  return formatUtcDate(current);
}

function weekEndForStart(weekStart: string): string {
  const parts = dateParts(weekStart);
  if (!parts) throw new Error(`Invalid evidence date: ${weekStart}`);
  const end = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2]));
  end.setUTCDate(end.getUTCDate() + 6);
  return formatUtcDate(end);
}

function isBlockedTrade(trade: InstrumentTrade): boolean {
  const status = String(trade.status ?? "").toLowerCase();
  return Boolean(trade.blocked_reason) ||
    status === "blocked" ||
    status === "rejected" ||
    Number(trade.filled) <= 0;
}

export function groupWeeklyEvidence(
  charts: InstrumentChartResponse[],
): WeeklyEvidenceWeek[] {
  const grouped = new Map<string, WeeklyEvidenceEvent[]>();

  const addEvent = (
    tsCode: string,
    record: InstrumentSignal | InstrumentTrade,
    kind: WeeklyEvidenceEvent["kind"],
    rawDate: unknown,
    blockedReason?: string,
  ): void => {
    const date = normalizeEvidenceDate(rawDate);
    if (!date) return;
    const weekStart = weekKeyForDate(date);
    const events = grouped.get(weekStart) ?? [];
    events.push({ kind, date, tsCode, record, ...(blockedReason ? { blockedReason } : {}) });
    grouped.set(weekStart, events);
  };

  for (const chart of charts) {
    for (const signal of chart.signals) {
      addEvent(chart.ts_code, signal, "signal", signal.date || signal.week_ending);
    }
    for (const trade of chart.trades) {
      addEvent(
        chart.ts_code,
        trade,
        isBlockedTrade(trade) ? "blocked" : "trade",
        trade.trade_date,
        trade.blocked_reason,
      );
    }
  }

  return [...grouped.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([weekStart, events]) => {
      events.sort(
        (left, right) =>
          left.date.localeCompare(right.date) ||
          left.tsCode.localeCompare(right.tsCode) ||
          left.kind.localeCompare(right.kind),
      );

      const chartByCode = new Map(charts.map((chart) => [chart.ts_code, chart]));
      const eventDatesByCode = new Map<string, string[]>();
      for (const event of events) {
        const dates = eventDatesByCode.get(event.tsCode) ?? [];
        dates.push(event.date);
        eventDatesByCode.set(event.tsCode, dates);
      }

      const instruments = [...eventDatesByCode.entries()]
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([tsCode, dates]) => ({
          tsCode,
          chart: chartByCode.get(tsCode)!,
          coreDates: { start: dates[0], end: dates[dates.length - 1] },
        }));

      return {
        weekStart,
        weekEnd: weekEndForStart(weekStart),
        instruments,
        events,
      };
    });
}

export function groupYearlyEvidence(
  charts: InstrumentChartResponse[],
): YearlyEvidenceYear[] {
  const grouped = new Map<string, WeeklyEvidenceEvent[]>();

  const addEvent = (
    tsCode: string,
    record: InstrumentSignal | InstrumentTrade,
    kind: WeeklyEvidenceEvent["kind"],
    rawDate: unknown,
    blockedReason?: string,
  ): void => {
    const date = normalizeEvidenceDate(rawDate);
    if (!date) return;
    const year = date.slice(0, 4);
    const events = grouped.get(year) ?? [];
    events.push({ kind, date, tsCode, record, ...(blockedReason ? { blockedReason } : {}) });
    grouped.set(year, events);
  };

  for (const chart of charts) {
    for (const signal of chart.signals) {
      addEvent(chart.ts_code, signal, "signal", signal.date || signal.week_ending);
    }
    for (const trade of chart.trades) {
      addEvent(
        chart.ts_code,
        trade,
        isBlockedTrade(trade) ? "blocked" : "trade",
        trade.trade_date,
        trade.blocked_reason,
      );
    }
  }

  const chartByCode = new Map(charts.map((chart) => [chart.ts_code, chart]));
  return [...grouped.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([year, events]) => {
      events.sort(
        (left, right) =>
          left.date.localeCompare(right.date) ||
          left.tsCode.localeCompare(right.tsCode) ||
          left.kind.localeCompare(right.kind),
      );
      const codes = [...new Set(events.map((event) => event.tsCode))].sort();
      return {
        year,
        instruments: codes
          .map((tsCode) => ({ tsCode, chart: chartByCode.get(tsCode) }))
          .filter((item): item is YearlyEvidenceInstrument => Boolean(item.chart)),
        events,
      };
    });
}
