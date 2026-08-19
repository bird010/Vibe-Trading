import { describe, expect, it } from "vitest";
import {
  groupYearlyEvidence,
  groupWeeklyEvidence,
  normalizeEvidenceDate,
  weekKeyForDate,
} from "../weeklyEvidence";
import type {
  InstrumentChartResponse,
  InstrumentSignal,
  InstrumentTrade,
} from "../types";

const baseChart = (tsCode: string): InstrumentChartResponse => ({
  run_id: "run-1",
  ts_code: tsCode,
  signals: [],
  trades: [],
  ohlcv: [],
  positions: [],
  orders: [],
  ohlcv_source: { available: true },
  mode: "RESEARCH_ONLY",
});

const signal = (date: string, target_weight: number): InstrumentSignal => ({
  date,
  target_weight,
});

const trade = (
  trade_date: string,
  action: "BUY" | "SELL",
  extra: Partial<InstrumentTrade> = {},
): InstrumentTrade => ({
  trade_date,
  action,
  filled: action === "BUY" ? 100 : 50,
  price: 3.5,
  ...extra,
});

describe("weekly ETF evidence aggregation", () => {
  it("groups operated instruments by year and keeps all events for that year", () => {
    const years = groupYearlyEvidence([
      {
        ...baseChart("510300.SH"),
        signals: [signal("20240109", 0.2), signal("20250106", 0.3)],
        trades: [trade("20240110", "BUY")],
      },
      {
        ...baseChart("159915.SZ"),
        trades: [trade("20241230", "SELL")],
      },
    ]);

    expect(years.map((year) => year.year)).toEqual(["2024", "2025"]);
    expect(years[0].instruments.map((item) => item.tsCode)).toEqual([
      "159915.SZ",
      "510300.SH",
    ]);
    expect(years[0].events.map((event) => event.date)).toEqual([
      "20240109",
      "20240110",
      "20241230",
    ]);
    expect(years[1].instruments.map((item) => item.tsCode)).toEqual(["510300.SH"]);
  });

  it("normalizes mixed date formats and calculates the ISO week Monday", () => {
    expect(normalizeEvidenceDate("2024-01-08")).toBe("20240108");
    expect(normalizeEvidenceDate("20240108")).toBe("20240108");
    expect(normalizeEvidenceDate(20240108.0)).toBe("20240108");
    expect(weekKeyForDate("20240110")).toBe("20240108");
  });

  it("groups signals and trades by week in stable order", () => {
    const weeks = groupWeeklyEvidence([
      {
        ...baseChart("159915.SZ"),
        signals: [signal("20240109", 0.2)],
        trades: [trade("20240110", "BUY")],
      },
      {
        ...baseChart("510300.SH"),
        signals: [signal("2024-01-08", 0.8)],
        trades: [trade("20240108", "SELL")],
      },
    ]);

    expect(weeks.map((week) => [week.weekStart, week.weekEnd])).toEqual([
      ["20240108", "20240114"],
    ]);
    expect(weeks[0].instruments.map((item) => item.tsCode)).toEqual([
      "159915.SZ",
      "510300.SH",
    ]);
    expect(
      weeks[0].events.map((event) => `${event.date}:${event.tsCode}:${event.kind}`),
    ).toEqual([
      "20240108:510300.SH:signal",
      "20240108:510300.SH:trade",
      "20240109:159915.SZ:signal",
      "20240110:159915.SZ:trade",
    ]);
    expect(weeks[0].instruments[0].coreDates).toEqual({
      start: "20240109",
      end: "20240110",
    });
    expect(weeks[0].events[1].record).toEqual(
      expect.objectContaining({ trade_date: "20240108" }),
    );
  });

  it("uses week_ending when a signal has no date", () => {
    const weeks = groupWeeklyEvidence([
      {
        ...baseChart("510300.SH"),
        signals: [{ week_ending: "20240114", target_weight: 0.5 }],
      },
    ]);

    expect(weeks[0].weekStart).toBe("20240108");
    expect(weeks[0].events[0]).toMatchObject({
      kind: "signal",
      date: "20240114",
      tsCode: "510300.SH",
    });
  });

  it("marks blocked trades separately from filled and partial trades", () => {
    const weeks = groupWeeklyEvidence([
      {
        ...baseChart("510300.SH"),
        trades: [
          trade("20240108", "BUY", {
            blocked_reason: "no adv",
            filled: 0,
            status: "BLOCKED",
          }),
          trade("20240109", "SELL", { filled: 10, status: "PARTIAL" }),
          trade("20240110", "BUY", { filled: 0 }),
        ],
      },
    ]);

    expect(weeks[0].events.map((event) => event.kind)).toEqual([
      "blocked",
      "trade",
      "blocked",
    ]);
    expect(weeks[0].events[0]).toMatchObject({
      blockedReason: "no adv",
      record: expect.objectContaining({ status: "BLOCKED" }),
    });
  });
});
