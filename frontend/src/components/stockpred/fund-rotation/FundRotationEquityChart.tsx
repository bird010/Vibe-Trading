import { useEffect, useMemo, useRef } from "react";
import { getChartTheme } from "@/lib/chart-theme";
import { echarts } from "@/lib/echarts";
import { useDarkMode } from "@/hooks/useDarkMode";
import type { ComparisonEquityData } from "./types";

interface Props {
  equity: ComparisonEquityData;
  height?: number;
}

function normalize(values: number[]): number[] {
  const first = values.find((value) => Number.isFinite(value) && value !== 0);
  if (first === undefined) return values.map(() => Number.NaN);
  return values.map((value) =>
    Number.isFinite(value) ? value / first : Number.NaN,
  );
}

function computeDrawdown(values: number[]): number[] {
  let peak = Number.NEGATIVE_INFINITY;
  return values.map((value) => {
    if (!Number.isFinite(value)) return Number.NaN;
    peak = Math.max(peak, value);
    return peak > 0 ? (value / peak - 1) * 100 : 0;
  });
}

export function FundRotationEquityChart({ equity, height = 420 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { dark } = useDarkMode();
  const normalized = useMemo(
    () =>
      Object.entries(equity.series)
        .map(([name, values]) => ({ name, values: normalize(values) }))
        .filter((entry) => entry.values.some(Number.isFinite)),
    [equity],
  );

  useEffect(() => {
    if (!ref.current || equity.dates.length === 0 || normalized.length === 0) return;
    const theme = getChartTheme();
    const chart = echarts.init(ref.current);
    const strategy = normalized.find((entry) => entry.name === "strategy") ?? normalized[0];
    const drawdown = computeDrawdown(strategy.values);
    const finiteDrawdown = drawdown.filter(Number.isFinite);
    const minimumDrawdown = finiteDrawdown.length > 0 ? Math.min(0, ...finiteDrawdown) : 0;

    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross" },
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontSize: 11 },
      },
      toolbox: {
        feature: {
          saveAsImage: { title: "保存" },
          restore: { title: "重置" },
        },
        right: 8,
        top: 0,
        iconStyle: { borderColor: theme.textColor },
      },
      legend: {
        data: normalized.map((entry) => entry.name),
        textStyle: { color: theme.textColor, fontSize: 11 },
        top: 4,
        left: 8,
        right: 96,
        type: "scroll",
      },
      grid: [
        { left: 12, right: 12, top: 44, height: "56%", containLabel: true },
        { left: 12, right: 12, top: "72%", height: "18%", containLabel: true },
      ],
      xAxis: [
        {
          type: "category",
          data: equity.dates,
          gridIndex: 0,
          boundaryGap: false,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { color: theme.textColor, fontSize: 10 },
        },
        {
          type: "category",
          data: equity.dates,
          gridIndex: 1,
          boundaryGap: false,
          axisLine: { lineStyle: { color: theme.axisColor } },
          axisLabel: { show: false },
        },
      ],
      yAxis: [
        {
          type: "value",
          gridIndex: 0,
          scale: true,
          splitLine: { lineStyle: { color: theme.gridColor } },
          axisLabel: {
            color: theme.textColor,
            fontSize: 10,
            formatter: (value: number) => value.toFixed(2),
          },
        },
        {
          type: "value",
          gridIndex: 1,
          max: 0,
          min: Math.min(minimumDrawdown * 1.1, -1),
          splitLine: { lineStyle: { color: theme.gridColor } },
          axisLabel: {
            color: theme.textColor,
            fontSize: 10,
            formatter: "{value}%",
          },
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1] },
        { type: "slider", xAxisIndex: [0, 1], bottom: 0, height: 16 },
      ],
      series: [
        ...normalized.map((entry, index) => ({
          name: entry.name,
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: entry.values,
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: index === 0 || entry.name === "strategy" ? 2.2 : 1.3 },
          emphasis: { focus: "series" },
        })),
        {
          name: `${strategy.name} 回撤`,
          type: "line",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: drawdown,
          showSymbol: false,
          lineStyle: { color: theme.downColor, width: 1 },
          areaStyle: { opacity: 0.12 },
        },
      ],
    });

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [dark, equity.dates, normalized]);

  if (equity.dates.length === 0 || normalized.length === 0) {
    return (
      <div className="rounded border bg-muted/20 px-3 py-6 text-center text-sm text-muted-foreground">
        当前回测没有可用净值数据。
      </div>
    );
  }

  return <div ref={ref} style={{ height }} />;
}
