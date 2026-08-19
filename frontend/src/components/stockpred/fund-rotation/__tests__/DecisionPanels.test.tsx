import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PortfolioChangeChart } from "../rebalance/PortfolioChangeChart";
import { WhyDecisionPanel } from "../rebalance/WhyDecisionPanel";
import { ExecutionSummary } from "../rebalance/ExecutionSummary";
import { RankingLane } from "../rebalance/RankingLane";
import { ClusterRepresentativeMap } from "../rebalance/ClusterRepresentativeMap";
import type { RebalanceDecisionResponse } from "../types";

const decision: RebalanceDecisionResponse = {
  run_id: "run-1",
  signal_date: "20240103",
  sequence: 1,
  quality: { decision_status: "REJECTED", reasons: ["MAX_CLUSTER_SHARE"] },
  before: { as_of_date: "20240102", weights: { "510300.SH": 0.5, "518880.SH": 0.5 }, cash_weight: 0 },
  after_target: { as_of_signal_date: "20240103", weights: { "159915.SZ": 0.5, "518880.SH": 0.5 }, cash_weight: 0 },
  decision: {
    strategy: { name: "代表 ETF", ranking_metric: "Momentum", selection_rule: "Top 3", weighting_rule: "Equal Weight" },
    cluster_snapshot: { snapshot_date: "20240103", overall: "REJECTED", max_cluster_share: 0.896, effective_cluster_count: 1.67 },
    candidates: [
      { ts_code: "159915.SZ", stages: { universe_eligible: true, cluster_id: 1, cluster_representative: true, ranking_eligible: true, rank: 1, portfolio_selected: true }, primary_metric: { id: "momentum", label: "Momentum", value: 0.82 }, previous_weight: 0, target_weight: 0.5 },
      { ts_code: "510300.SH", stages: { universe_eligible: true, cluster_id: 2, cluster_representative: true, ranking_eligible: true, rank: 7, portfolio_selected: false }, primary_metric: { id: "momentum", label: "Momentum", value: 0.42 }, previous_weight: 0.5, target_weight: 0, exclusion_stage: "PORTFOLIO", exclusion_reason: "DROP" },
    ],
  },
  execution: {
    orders: [{ ts_code: "159915.SZ", action: "BUY", status: "FILLED" }],
    fills: [],
    summary: { filled: 1, partial: 0, blocked: 0, commission: 12.5, turnover: 0.5 },
  },
};

describe("fund rotation decision panels", () => {
  it("renders NEW and DROP portfolio changes", () => {
    render(<PortfolioChangeChart before={decision.before} afterTarget={decision.after_target} onInstrumentClick={vi.fn()} />);
    expect(screen.getByText("NEW")).toBeDefined();
    expect(screen.getByText("DROP")).toBeDefined();
  });

  it("places dumbbell endpoints at their actual weight percentages", () => {
    const { container } = render(
      <PortfolioChangeChart
        before={{ weights: { "ETF-A": 0.1 }, cash_weight: 0.9 }}
        afterTarget={{ weights: { "ETF-A": 0.4 }, cash_weight: 0.6 }}
        onInstrumentClick={vi.fn()}
      />,
    );
    expect(container.querySelector('[data-testid="before-marker-ETF-A"]')).toHaveStyle({ left: "10%" });
    expect(container.querySelector('[data-testid="target-marker-ETF-A"]')).toHaveStyle({ left: "40%" });
  });

  it("renders pipeline, rejected cluster quality, and ranking lane", () => {
    render(<WhyDecisionPanel decision={decision} candidateView="all" onCandidateViewChange={vi.fn()} onInstrumentClick={vi.fn()} />);
    expect(screen.getAllByText("Strategy Score Ranking").length).toBeGreaterThan(0);
    expect(screen.getByText("Cluster Quality")).toBeDefined();
    expect(screen.getByText("REJECTED")).toBeDefined();
    expect(screen.getByText("TOP 3 CUTOFF")).toBeDefined();
  });

  it("uses structured top_n and keeps score components attached to their subject", () => {
    const withComponents = {
      ...decision,
      decision: {
        ...decision.decision,
        strategy: { ...decision.decision.strategy, selection_rule: "legacy text", top_n: 2 },
        candidates: decision.decision.candidates.map((candidate, index) => ({
          ...candidate,
          score: {
            value: index === 0 ? 0.08 : 0.02,
            direction: "HIGHER_BETTER" as const,
            components: { momentum: index === 0 ? 0.08 : 0.02 },
          },
        })),
      },
    };
    render(<WhyDecisionPanel decision={withComponents} candidateView="all" onCandidateViewChange={vi.fn()} onInstrumentClick={vi.fn()} />);
    expect(screen.getByText("TOP 2 CUTOFF")).toBeDefined();
    expect(screen.getByText(/momentum: 0\.080/)).toBeDefined();
    expect(screen.getByText(/momentum: 0\.020/)).toBeDefined();
  });

  it("renders execution fill summary", () => {
    render(<ExecutionSummary execution={decision.execution} before={decision.before} afterTarget={decision.after_target} />);
    expect(screen.getByText(/执行摘要/)).toBeDefined();
    expect(screen.getByText(/Filled：1/)).toBeDefined();
  });

  it("sorts eligible candidates by rank and inserts cutoff after top N", () => {
    const { container } = render(
      <RankingLane
        candidates={[7, 2, 4, 1, 3].map((rank) => ({
          ts_code: `ETF-${rank}`,
          stages: { universe_eligible: true, cluster_representative: true, ranking_eligible: true, rank, portfolio_selected: rank <= 3 },
          primary_metric: { id: "legacy", label: "Momentum", value: rank === 1 ? 4 : rank === 7 ? -2 : rank / 10 },
          score: { value: rank === 1 ? 4 : rank === 7 ? -2 : rank / 10, direction: "HIGHER_BETTER" },
          previous_weight: 0,
          target_weight: rank <= 3 ? 0.33 : 0,
        }))}
        topN={3}
        primaryMetric="Strategy Score"
        view="all"
        onInstrumentClick={vi.fn()}
      />,
    );
    const rowLabels = Array.from(container.querySelectorAll("button")).map((row) => row.textContent ?? "");
    expect(rowLabels.map((label) => label.match(/#(\d+)/)?.[1])).toEqual(["1", "2", "3", "4", "7"]);
    const text = container.textContent ?? "";
    expect(text.indexOf("#3")).toBeLessThan(text.indexOf("TOP 3 CUTOFF"));
    expect(text.indexOf("TOP 3 CUTOFF")).toBeLessThan(text.indexOf("#4"));
    const widths = Array.from(container.querySelectorAll("button span span")).map((node) => (node as HTMLElement).style.width);
    expect(widths[0]).toBe("100%");
    expect(widths[4]).toBe("3%");
  });

  it("keeps a held ineligible score visible as a DROP explanation", () => {
    render(
      <RankingLane
        candidates={[{
          ts_code: "ETF-DROP",
          stages: { universe_eligible: true, cluster_representative: true, ranking_eligible: false, rank: null, portfolio_selected: false },
          score: { value: -0.03, direction: "HIGHER_BETTER" },
          before_weight: 0.5,
          target_weight: 0,
        }]}
        topN={3}
        primaryMetric="Strategy Score"
        view="all"
        onInstrumentClick={vi.fn()}
      />,
    );
    expect(screen.getByText(/DROP · SCORE_INELIGIBLE/)).toBeDefined();
    expect(screen.getByText(/-0.03/)).toBeDefined();
  });

  it("does not open an instrument chart for the cash pseudo-position", () => {
    const onInstrumentClick = vi.fn();
    render(<PortfolioChangeChart before={{ weights: { _CASH: 1 }, cash_weight: 1 }} afterTarget={{ weights: { "ETF-A": 1 }, cash_weight: 0 }} onInstrumentClick={onInstrumentClick} />);
    fireEvent.click(screen.getByText("_CASH"));
    expect(onInstrumentClick).not.toHaveBeenCalled();
  });

  it("expands hidden cluster members", () => {
    render(
      <ClusterRepresentativeMap
        candidates={["A", "B", "C", "D"].map((ts_code) => ({
          ts_code,
          stages: { universe_eligible: true, cluster_id: 1, cluster_representative: ts_code === "A", ranking_eligible: true, portfolio_selected: false },
          previous_weight: 0,
          target_weight: 0,
        }))}
      />,
    );
    expect(screen.queryByText("D")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "+1 more" }));
    expect(screen.getByText("D")).toBeDefined();
  });
});
