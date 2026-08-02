import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockState = vi.hoisted(() => ({
    defaults: { params: { top_n: 3 }, schema_version: "v1", mode: "RESEARCH_ONLY" },
    runs: [], activeRunId: "run-1", activeRun: { run_id: "run-1", stage: "SUCCEEDED" },
    loading: false, error: null, events: [],
    fetchDefaults: vi.fn(), fetchRuns: vi.fn(), submitBacktest: vi.fn(), selectRun: vi.fn(),
}));

vi.mock("../fund-rotation/useFundRotation", () => ({
  useFundRotation: () => mockState,
}));

import { FundRotationTab } from "../fund-rotation/FundRotationTab";

describe("FundRotationTab research views", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      json: async () => null,
      text: async () => null,
    }));
  });

  it("renders all six approved result views", () => {
    render(<FundRotationTab />);
    for (const label of ["概览", "持仓", "聚类", "交易核验", "成交诊断", "数据质量"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByText("动量最强簇数 N")).toBeInTheDocument();
  });

  it("joins parent orders with attempts and renders blocked events off-price", async () => {
    const tradeCsv = "index,trade_date,ts_code,action,status,requested,filled,unfilled,reason,price,order_id,attempt_id,remaining\n0,20240122,510300.SH,BUY,BLOCKED,1000,0,1000,market_blocked,0,SIG-1-510300.SH,SIG-1-510300.SH-A1,1000\n1,20240123,510300.SH,BUY,FILLED,1000,1000,0,,10.01,SIG-1-510300.SH,SIG-1-510300.SH-A2,0";
    const orderCsv = "index,order_id,ts_code,direction,requested,attempt_number,trade_date,attempt_filled,attempt_status,remaining,final_status\n0,SIG-1-510300.SH,510300.SH,BUY,1000,1,20240122,0,BLOCKED,1000,FILLED";
    const targetCsv = "index,week_ending,ts_code,weight,previous_weight,signal_action\n0,20240119,510300.SH,0,1,EXIT";
    const chart = {
      ts_code: "510300.SH",
      ohlcv: [
        { trade_date: "20240122", open: 10, high: 11, low: 9, close: 10, vol: 1000 },
        { trade_date: "20240123", open: 10, high: 11, low: 9, close: 10.2, vol: 1000 },
      ],
      signals: [{ week_ending: "20240122", weight: 0, previous_weight: 1, signal_action: "EXIT" }],
      trades: [
        { trade_date: "20240122", action: "BUY", status: "BLOCKED", requested: 1000, filled: 0, price: 0, reason: "market_blocked", order_id: "SIG-1-510300.SH", attempt_id: "SIG-1-510300.SH-A1", remaining: 1000 },
        { trade_date: "20240123", action: "BUY", status: "FILLED", requested: 1000, filled: 1000, price: 10.01, order_id: "SIG-1-510300.SH", attempt_id: "SIG-1-510300.SH-A2", remaining: 0 },
        { trade_date: "20240123", action: "SHARE_ADJUSTMENT", status: "APPLIED", event_type: "CORPORATE_ACTION", corporate_action_id: "CA-1", requested: 1000, filled: 2000, price: 0, order_id: "", attempt_id: "", old_adj_factor: 1, new_adj_factor: 2 },
      ],
      orders: [{ order_id: "SIG-1-510300.SH", direction: "BUY", requested: 1000, final_status: "FILLED" }],
      positions: [],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/instruments/")) return { ok: true, json: async () => chart, text: async () => "" };
      if (url.includes("trade_events.csv")) return { ok: true, text: async () => tradeCsv, json: async () => null };
      if (url.includes("orders.csv")) return { ok: true, text: async () => orderCsv, json: async () => null };
      if (url.includes("targets.csv")) return { ok: true, text: async () => targetCsv, json: async () => null };
      return { ok: false, json: async () => null, text: async () => null };
    }));

    const { container } = render(<FundRotationTab />);
    fireEvent.click(screen.getByRole("button", { name: "交易核验" }));
    await waitFor(() => expect(screen.getByLabelText("order-attempt-lifecycle")).toBeInTheDocument());
    expect(screen.getByText("PARENT_ORDER")).toBeInTheDocument();
    expect(screen.getAllByText("ATTEMPT")).toHaveLength(2);
    expect(container.querySelector('[data-marker="blocked"]')).toBeInTheDocument();
    expect(container.querySelector('[data-marker="share-adjustment"]')).toBeInTheDocument();
    expect(screen.queryByText("CA-1")).not.toBeInTheDocument();
    expect(screen.getByText(/EXIT/)).toBeInTheDocument();
  });

  it("preserves quoted corporate-action JSON from pandas orders CSV", async () => {
    const adjustmentLedger = '[{"corporate_action_id":"CA-1","after":{"filled":2000}},{"corporate_action_id":"CA-2","after":{"filled":1000}}]';
    const quotedLedger = adjustmentLedger.replaceAll('"', '""');
    const orderCsv = [
      "index,order_id,ts_code,direction,requested,corporate_action_adjustments,reason",
      `0,SIG-1-510300.SH,510300.SH,BUY,1000,"${quotedLedger}",`,
    ].join("\r\n");
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("orders.csv")) return { ok: true, text: async () => orderCsv, json: async () => null };
      return { ok: false, json: async () => null, text: async () => null };
    }));

    render(<FundRotationTab />);
    fireEvent.click(screen.getByRole("button", { name: "成交诊断" }));
    await waitFor(() => expect(screen.getByText(adjustmentLedger)).toBeInTheDocument());
  });
});
