import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RotationAnalysisTab } from "../RotationAnalysisTab";
import { initialRebalanceSignalDate } from "../useRotationAnalysis";

const mockState = vi.hoisted(() => ({
  timeline: {
    run_id: "run-1",
    start_date: "20240102",
    end_date: "20240103",
    instruments: [],
    intervals: [],
    rebalance_markers: [],
  },
  rebalanceIndex: { run_id: "run-1", items: [] },
  rebalanceDetails: {},
  selectedSignalDate: null,
  timelineWindow: { start: "20240102", end: "20240103" },
  candidateView: "changed" as const,
  loading: false,
  error: null,
  decisionLoading: false,
  decisionErrors: {},
  openRun: vi.fn(),
  selectSignalDate: vi.fn(),
  setTimelineWindow: vi.fn(),
  setCandidateView: vi.fn(),
  reset: vi.fn(),
}));

vi.mock("../useRotationAnalysis", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../useRotationAnalysis")>()),
  useRotationAnalysis: () => mockState,
}));

describe("RotationAnalysisTab", () => {
  beforeEach(() => {
    mockState.openRun.mockReset();
  });

  it("renders the rotation analysis sections", () => {
    render(<RotationAnalysisTab runId="run-1" />);
    expect(screen.getByText("轮动分析")).toBeDefined();
    expect(screen.getByText("持仓与权重变化")).toBeDefined();
    expect(screen.getByText("调仓决策")).toBeDefined();
    expect(mockState.openRun).toHaveBeenCalledWith("run-1");
  });

  it("chooses the most recent rebalance with actual execution change", () => {
    expect(initialRebalanceSignalDate([
      { signal_date: "20240105", sequence: 1, quality_status: "VALID", changed_positions: 0, actual_changed_positions: 0, has_execution: true },
      { signal_date: "20240112", sequence: 2, quality_status: "VALID", changed_positions: 2, actual_changed_positions: 2, has_execution: true },
    ])).toBe("20240112");
  });
});
