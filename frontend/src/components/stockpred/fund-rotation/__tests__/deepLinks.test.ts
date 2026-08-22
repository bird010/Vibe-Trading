import { describe, expect, it } from "vitest";
import { readFundRotationUrl, updateFundRotationUrl } from "../deepLinks";

describe("fund rotation deep links", () => {
  it("accepts the cluster interval detail tab", () => {
    const state = readFundRotationUrl("https://example.test/funds?run_id=run-1&detail_tab=cluster_interval");

    expect(state.tab).toBe("cluster_interval");
  });

  it("parses rotation and chart state from the documented query contract", () => {
    const state = readFundRotationUrl("https://example.test/funds?run_id=run-1&detail_tab=chart&instrument=159915.SZ&focus_date=20240801&strategy_indicator=momentum");
    expect(state).toEqual({ runId: "run-1", tab: "chart", signalDate: null, instrument: "159915.SZ", focusDate: "20240801", strategyScore: "momentum" });
  });

  it("updates only fund-rotation query keys and keeps unrelated params", () => {
    const next = updateFundRotationUrl(
      "https://example.test/funds?foo=bar&run_id=old&detail_tab=overview",
      { runId: "run-2", tab: "rotation_analysis", signalDate: "20240801" },
    );
    expect(next).toBe("https://example.test/funds?foo=bar&run_id=run-2&detail_tab=rotation_analysis&signal_date=20240801");
  });
});
