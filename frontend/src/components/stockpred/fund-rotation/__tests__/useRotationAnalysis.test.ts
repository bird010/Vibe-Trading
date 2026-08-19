import { act } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useRotationAnalysis } from "../useRotationAnalysis";
import {
  fetchHoldingsTimeline,
  fetchRebalanceDecision,
  fetchRebalanceIndex,
} from "../api";

vi.mock("../api", () => ({
  fetchHoldingsTimeline: vi.fn(),
  fetchRebalanceDecision: vi.fn(),
  fetchRebalanceIndex: vi.fn(),
}));

const timeline = { run_id: "run-1", intervals: [], instruments: [], rebalance_markers: [] };
const index = { run_id: "run-1", items: [] };

describe("useRotationAnalysis", () => {
  beforeEach(() => {
    vi.mocked(fetchHoldingsTimeline).mockReset();
    vi.mocked(fetchRebalanceIndex).mockReset();
    vi.mocked(fetchRebalanceDecision).mockReset();
    useRotationAnalysis.getState().reset();
  });

  it("loads timeline and index in parallel for a run", async () => {
    vi.mocked(fetchHoldingsTimeline).mockResolvedValue(timeline);
    vi.mocked(fetchRebalanceIndex).mockResolvedValue(index);

    await act(async () => {
      await useRotationAnalysis.getState().openRun("run-1");
    });

    expect(fetchHoldingsTimeline).toHaveBeenCalledWith("run-1", expect.any(AbortSignal));
    expect(fetchRebalanceIndex).toHaveBeenCalledWith("run-1", expect.any(AbortSignal));
    expect(useRotationAnalysis.getState().timeline).toEqual(timeline);
    expect(useRotationAnalysis.getState().rebalanceIndex).toEqual(index);
  });

  it("caches a decision bundle by signal date", async () => {
    const bundle = { run_id: "run-1", signal_date: "20240103" };
    vi.mocked(fetchHoldingsTimeline).mockResolvedValue(timeline);
    vi.mocked(fetchRebalanceIndex).mockResolvedValue(index);
    vi.mocked(fetchRebalanceDecision).mockResolvedValue(bundle);

    await act(async () => {
      await useRotationAnalysis.getState().openRun("run-1");
      await useRotationAnalysis.getState().selectSignalDate("20240103");
      await useRotationAnalysis.getState().selectSignalDate("20240103");
    });

    expect(fetchRebalanceDecision).toHaveBeenCalledTimes(1);
    expect(useRotationAnalysis.getState().rebalanceDetails["20240103"]).toEqual(bundle);
  });
});
