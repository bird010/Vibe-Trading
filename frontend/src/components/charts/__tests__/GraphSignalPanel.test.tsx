import { render, screen } from "@testing-library/react";
import { GraphSignalPanel } from "../GraphSignalPanel";
import type { GraphSignalPoint } from "@/lib/api";


const chartMock = vi.hoisted(() => ({
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  group: "",
}));

vi.mock("@/lib/echarts", () => ({
  echarts: { init: vi.fn(() => chartMock) },
  CHART_GROUP: "quant-charts",
  connectCharts: vi.fn(),
}));
vi.mock("@/lib/chart-theme", () => ({
  getChartTheme: () => ({
    textColor: "#666",
    axisColor: "#333",
    gridColor: "#ddd",
    infoColor: "#00f",
    warningColor: "#f90",
    tooltipBg: "#fff",
    tooltipBorder: "#ddd",
    tooltipText: "#111",
  }),
}));
vi.mock("@/hooks/useDarkMode", () => ({ useDarkMode: () => ({ dark: false }) }));

const POINTS: GraphSignalPoint[] = [
  {
    time: "2025-01-03",
    code: "000001.SZ",
    score: 0.82,
    rank: 7,
    direction: "long",
    stage: "expansion",
    action: "buy",
    risk_adjustment: -0.1,
  },
];

describe("GraphSignalPanel", () => {
  beforeEach(() => {
    chartMock.setOption.mockReset();
    chartMock.resize.mockReset();
    chartMock.dispose.mockReset();
  });

  it("renders score diagnostics without a price axis", () => {
    render(<GraphSignalPanel symbol="000001.SZ" points={POINTS} />);

    expect(screen.getByText(/rank 7/i)).toBeInTheDocument();
    expect(screen.getByText(/expansion/i)).toBeInTheDocument();
    expect(chartMock.setOption).toHaveBeenCalledWith(
      expect.objectContaining({
        yAxis: expect.objectContaining({ name: "Score" }),
      }),
    );
    expect(JSON.stringify(chartMock.setOption.mock.calls[0][0])).not.toContain("candlestick");
  });

  it("renders the localized empty state without initializing ECharts", () => {
    render(<GraphSignalPanel symbol="000001.SZ" points={[]} />);

    expect(screen.getByText("No Graph signal data")).toBeInTheDocument();
    expect(chartMock.setOption).not.toHaveBeenCalled();
  });

  it("disposes the chart and resize observer on unmount", () => {
    const disconnect = vi.spyOn(ResizeObserver.prototype, "disconnect");
    const view = render(<GraphSignalPanel symbol="000001.SZ" points={POINTS} />);

    view.unmount();

    expect(chartMock.dispose).toHaveBeenCalled();
    expect(disconnect).toHaveBeenCalled();
  });
});
