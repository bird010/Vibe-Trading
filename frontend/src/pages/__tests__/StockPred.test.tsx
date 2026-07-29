import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { StockPred } from "../StockPred";

const apiMock = vi.hoisted(() => ({
  getStockPredStatus: vi.fn(),
  listStockPredStrategies: vi.fn(),
  listRecentStrategyBatches: vi.fn(),
  listUnfinishedStrategyBatches: vi.fn(),
  createStrategyBatch: vi.fn(),
  getStrategyBatch: vi.fn(),
  strategyBatchStreamUrl: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMock }));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

class EventSourceMock {
  static instances: EventSourceMock[] = [];
  listeners = new Map<string, (event: MessageEvent) => void>();
  close = vi.fn();

  constructor(public readonly url: string) {
    EventSourceMock.instances.push(this);
  }

  addEventListener(type: string, listener: EventListener) {
    this.listeners.set(type, listener as (event: MessageEvent) => void);
  }

  emit(type: string, body: unknown) {
    this.listeners.get(type)?.(new MessageEvent(type, { data: JSON.stringify(body) }));
  }
}

const READY_STATUS = {
  ready: true,
  contract: "stockpred-data/v1" as const,
  root: "E:/stockpred-data",
  as_of: "2025-03-31T15:00:00+08:00",
  tables: [{ name: "daily", status: "ready", max_date: "20250331" }],
};

const SAMPLE_STRATEGIES = [
  { id: "stockpred_graph", name: "StockPred Graph", kind: "graph" as const, zoo: null },
  { id: "alpha101_1", name: "Alpha One", kind: "alpha_zoo" as const, zoo: "alpha101" },
  { id: "gtja191_1", name: "GTJA 001", kind: "alpha_zoo" as const, zoo: "gtja191" },
];

function seedReadyApi() {
  apiMock.getStockPredStatus.mockResolvedValue(READY_STATUS);
  apiMock.listStockPredStrategies.mockResolvedValue(SAMPLE_STRATEGIES);
  apiMock.listRecentStrategyBatches.mockResolvedValue([]);
  apiMock.listUnfinishedStrategyBatches.mockResolvedValue([]);
}

function renderStockPred() {
  return render(<StockPred />, { wrapper: MemoryRouter });
}

describe("StockPred page", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((mock) => mock.mockReset());
    EventSourceMock.instances = [];
    vi.stubGlobal("EventSource", EventSourceMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("disables start when the StockPred contract is not ready", async () => {
    apiMock.getStockPredStatus.mockResolvedValue({
      ...READY_STATUS,
      ready: false,
      root: "",
      error_code: "STOCKPRED_ROOT_MISSING",
    });
    apiMock.listStockPredStrategies.mockResolvedValue(SAMPLE_STRATEGIES);
    apiMock.listRecentStrategyBatches.mockResolvedValue([]);
    apiMock.listUnfinishedStrategyBatches.mockResolvedValue([]);

    renderStockPred();

    expect(await screen.findByRole("button", { name: "stockPred.start" })).toBeDisabled();
  });

  it("disables start when no strategy is selected", async () => {
    seedReadyApi();
    renderStockPred();

    expect(await screen.findByRole("button", { name: "stockPred.start" })).toBeDisabled();
  });

  it("enables research parameters and disables topN/evalStep in parity mode", async () => {
    seedReadyApi();
    const user = userEvent.setup();
    renderStockPred();

    expect(await screen.findByLabelText("stockPred.topN")).toBeDisabled();
    expect(screen.getByLabelText("stockPred.evalStep")).toBeDisabled();

    await user.selectOptions(screen.getByLabelText("stockPred.mode"), "research");
    expect(screen.getByLabelText("stockPred.topN")).toBeEnabled();
    expect(screen.getByLabelText("stockPred.evalStep")).toBeEnabled();
  });

  it("shows all strategies including graph in the unified config section", async () => {
    seedReadyApi();
    renderStockPred();

    expect(await screen.findByText("StockPred Graph")).toBeInTheDocument();
    expect(screen.getByText("Alpha One")).toBeInTheDocument();
    expect(screen.getByText("GTJA 001")).toBeInTheDocument();
  });

  it("select all checkbox toggles all strategies", async () => {
    seedReadyApi();
    const user = userEvent.setup();
    renderStockPred();

    await screen.findByText("StockPred Graph");

    const selectAllCheckbox = screen.getByLabelText("stockPred.selectAll");
    await user.click(selectAllCheckbox);
    const checkboxes = screen.getAllByRole<HTMLInputElement>("checkbox");
    expect(checkboxes.every((cb) => cb.checked)).toBe(true);

    await user.click(selectAllCheckbox);
    expect(checkboxes.every((cb) => !cb.checked)).toBe(true);
  });

  it("creates a strategy batch and opens SSE on run click", async () => {
    seedReadyApi();
    apiMock.createStrategyBatch.mockResolvedValue({ batch_id: "batch_123", events_url: "/events" });
    apiMock.getStrategyBatch.mockResolvedValue({
      batch_id: "batch_123", status: "running", phase: "screening",
      screening_done: 0, screening_total: 3, detail_done: 0, detail_total: 0,
      reports: [
        { strategy_id: "stockpred_graph", strategy_name: "StockPred Graph", status: "running", metrics: { sharpe: 0 } },
        { strategy_id: "alpha101_1", strategy_name: "Alpha One", status: "pending", metrics: {} },
        { strategy_id: "gtja191_1", strategy_name: "GTJA 001", status: "pending", metrics: {} },
      ],
    });
    const user = userEvent.setup();
    renderStockPred();

    await screen.findByText("StockPred Graph");
    await user.click(screen.getByLabelText("stockPred.selectAll"));
    await user.click(screen.getByRole("button", { name: "stockPred.start" }));

    expect(apiMock.createStrategyBatch).toHaveBeenCalledWith(
      expect.objectContaining({ strategy_ids: ["stockpred_graph", "alpha101_1", "gtja191_1"] }),
    );
    expect(EventSourceMock.instances).toHaveLength(1);
    expect(apiMock.getStrategyBatch).toHaveBeenCalledWith("batch_123", "sharpe");
  });

  it("refreshes batch detail on SSE progress event", async () => {
    seedReadyApi();
    apiMock.createStrategyBatch.mockResolvedValue({ batch_id: "batch_456", events_url: "/events" });
    apiMock.getStrategyBatch
      .mockResolvedValueOnce({
        batch_id: "batch_456", status: "running", phase: "screening",
        screening_done: 0, screening_total: 3, detail_done: 0, detail_total: 0, reports: [],
      })
      .mockResolvedValueOnce({
        batch_id: "batch_456", status: "running", phase: "screening",
        screening_done: 1, screening_total: 3, detail_done: 0, detail_total: 0,
        reports: [{ strategy_id: "alpha101_1", strategy_name: "Alpha One", status: "success", run_id: "strategy_1", metrics: { sharpe: 1.5 } }],
      });
    const user = userEvent.setup();
    renderStockPred();

    await screen.findByText("StockPred Graph");
    await user.click(screen.getByLabelText("stockPred.selectAll"));
    await user.click(screen.getByRole("button", { name: "stockPred.start" }));

    // SSE progress fires a refresh
    act(() => EventSourceMock.instances[0].emit("progress", {}));
    await vi.waitFor(() => expect(apiMock.getStrategyBatch).toHaveBeenCalledTimes(2));
  });

  it("closes SSE stream on done event", async () => {
    seedReadyApi();
    apiMock.createStrategyBatch.mockResolvedValue({ batch_id: "batch_done", events_url: "/events" });
    apiMock.getStrategyBatch.mockResolvedValue({
      batch_id: "batch_done", status: "completed", phase: "completed",
      screening_done: 3, screening_total: 3, detail_done: 0, detail_total: 0, reports: [],
    });
    const user = userEvent.setup();
    renderStockPred();

    await screen.findByText("StockPred Graph");
    await user.click(screen.getByLabelText("stockPred.selectAll"));
    await user.click(screen.getByRole("button", { name: "stockPred.start" }));

    const stream = EventSourceMock.instances[0];
    act(() => stream.emit("done", {}));
    await vi.waitFor(() => expect(stream.close).toHaveBeenCalled());
  });

  it("shows error on batch creation failure", async () => {
    seedReadyApi();
    apiMock.createStrategyBatch.mockRejectedValueOnce(new Error("batch start failed"));
    const user = userEvent.setup();
    renderStockPred();

    await screen.findByText("StockPred Graph");
    await user.click(screen.getByLabelText("stockPred.selectAll"));
    await user.click(screen.getByRole("button", { name: "stockPred.start" }));

    expect(await screen.findByText("batch start failed")).toBeInTheDocument();
  });

  it("cleans up SSE stream and poll timer on unmount", async () => {
    seedReadyApi();
    apiMock.createStrategyBatch.mockResolvedValue({ batch_id: "batch_umount", events_url: "/events" });
    apiMock.getStrategyBatch.mockResolvedValue({
      batch_id: "batch_umount", status: "running", phase: "screening",
      screening_done: 1, screening_total: 3, detail_done: 0, detail_total: 0, reports: [],
    });
    const user = userEvent.setup();
    const view = renderStockPred();

    await screen.findByText("StockPred Graph");
    await user.click(screen.getByLabelText("stockPred.selectAll"));
    await user.click(screen.getByRole("button", { name: "stockPred.start" }));

    expect(EventSourceMock.instances).toHaveLength(1);
    const stream = EventSourceMock.instances[0];
    view.unmount();
    expect(stream.close).toHaveBeenCalled();
  });

  it("shows running batches in the progress panel", async () => {
    seedReadyApi();
    apiMock.listUnfinishedStrategyBatches.mockResolvedValue([
      {
        batch_id: "batch_running_1", status: "running", phase: "screening",
        screening_done: 5, screening_total: 12, detail_done: 0, detail_total: 0,
        created_at: "2026-07-26T10:00:00Z", reports: [],
      },
    ]);
    renderStockPred();

    expect(await screen.findByText("batch_running_1")).toBeInTheDocument();
    expect(screen.getByText("running · screening")).toBeInTheDocument();
    expect(screen.getByText("5/12")).toBeInTheDocument();
  });

  it("polls for running batches on mount", async () => {
    seedReadyApi();
    renderStockPred();

    await screen.findByText("stockPred.progress");
    // Initial poll happens synchronously in useEffect
    expect(apiMock.listUnfinishedStrategyBatches).toHaveBeenCalled();
  });

  it("shows batches in Recent Runs and opens detail on click", async () => {
    seedReadyApi();
    apiMock.listRecentStrategyBatches.mockResolvedValue([
      {
        batch_id: "batch_history", status: "completed", phase: "completed",
        screening_done: 3, screening_total: 3, detail_done: 0, detail_total: 0,
        created_at: "2026-07-25T12:00:00Z", reports: [],
      },
    ]);
    apiMock.getStrategyBatch.mockResolvedValue({
      batch_id: "batch_history", status: "completed", phase: "completed",
      screening_done: 3, screening_total: 3, detail_done: 0, detail_total: 0,
      reports: [
        { strategy_id: "alpha101_1", strategy_name: "Alpha One", status: "success", run_id: "strategy_1", metrics: { sharpe: 1.2 } },
        { strategy_id: "gtja191_1", strategy_name: "GTJA 001", status: "failed", metrics: {}, detail_status: "failed", detail_reason: "no trades" },
      ],
    });
    const user = userEvent.setup();
    renderStockPred();

    await screen.findByText("batch_history");
    await user.click(screen.getByText("batch_history"));
    expect(await screen.findByText(/no trades/)).toBeInTheDocument();
    // Alpha One appears in both strategy list and batch detail, use getAllByText
    expect(screen.getAllByText("Alpha One").length).toBeGreaterThanOrEqual(2);
  });
});
