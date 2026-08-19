import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FundRotationStrategyEvidenceChart } from "../FundRotationStrategyEvidenceChart";
import type { StrategyEvidence } from "../types";

const evidence: StrategyEvidence = {
  benchmark: {
    ts_code: "000300.SH",
    name: "沪深300",
    normalized_price: [
      { date: "20240102", value: 1 },
      { date: "20240103", value: 1.02 },
    ],
  },
  indicators: [
    {
      id: "cluster_momentum",
      label: "Momentum",
      formula_id: "strategy.cluster_momentum",
      unit: "score",
      points: [
        { date: "20240102", value: 0.8 },
        { date: "20240103", value: 0.82 },
      ],
    },
  ],
  evidence_version: "1",
};

describe("FundRotationStrategyEvidenceChart", () => {
  it("renders backend indicators, benchmark and switches the active indicator", () => {
    render(<FundRotationStrategyEvidenceChart evidence={evidence} />);
    expect(screen.getByTestId("strategy-evidence-chart")).toBeDefined();
    expect(screen.getByRole("button", { name: "Momentum" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText(/沪深300/)).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "Momentum" }));
    expect(screen.getByText(/strategy\.cluster_momentum/)).toBeDefined();
  });

  it("shows an explicit unavailable state without strategy evidence", () => {
    render(<FundRotationStrategyEvidenceChart evidence={null} />);
    expect(screen.getByText(/策略证据暂无/)).toBeDefined();
  });
});
