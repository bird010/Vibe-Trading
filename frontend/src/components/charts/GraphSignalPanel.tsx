import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import type { GraphSignalPoint } from "@/lib/api";
import { getChartTheme } from "@/lib/chart-theme";
import { echarts, CHART_GROUP, connectCharts } from "@/lib/echarts";
import { useDarkMode } from "@/hooks/useDarkMode";


interface Props {
  symbol: string;
  points: GraphSignalPoint[];
  height?: number;
}

export function GraphSignalPanel({ symbol, points, height = 300 }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const { t } = useTranslation();
  const { dark } = useDarkMode();
  const latest = points[points.length - 1];

  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const theme = getChartTheme();
    const chart = echarts.init(ref.current);
    chart.group = CHART_GROUP;
    connectCharts();
    const hasRiskAdjustment = points.some((point) => point.risk_adjustment != null);

    chart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        backgroundColor: theme.tooltipBg,
        borderColor: theme.tooltipBorder,
        textStyle: { color: theme.tooltipText, fontSize: 11 },
      },
      legend: {
        data: [
          t("stockPred.score"),
          ...(hasRiskAdjustment ? [t("stockPred.riskAdjustment")] : []),
        ],
        textStyle: { color: theme.textColor, fontSize: 11 },
      },
      grid: { left: 12, right: 12, top: 40, bottom: 24, containLabel: true },
      xAxis: {
        type: "category",
        data: points.map((point) => point.time),
        axisLine: { lineStyle: { color: theme.axisColor } },
        axisLabel: { color: theme.textColor, fontSize: 10 },
      },
      yAxis: {
        type: "value",
        name: t("stockPred.score"),
        scale: true,
        nameTextStyle: { color: theme.textColor },
        axisLabel: { color: theme.textColor, fontSize: 10 },
        splitLine: { lineStyle: { color: theme.gridColor } },
      },
      series: [
        {
          name: t("stockPred.score"),
          type: "line",
          showSymbol: true,
          data: points.map((point) => point.score),
          lineStyle: { color: theme.infoColor, width: 2 },
          itemStyle: { color: theme.infoColor },
        },
        ...(hasRiskAdjustment
          ? [{
              name: t("stockPred.riskAdjustment"),
              type: "line" as const,
              showSymbol: true,
              data: points.map((point) => point.risk_adjustment ?? null),
              lineStyle: { color: theme.warningColor, width: 1.5 },
              itemStyle: { color: theme.warningColor },
            }]
          : []),
      ],
    });

    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [points, dark, t]);

  if (!latest) {
    return <div className="p-4 text-sm text-muted-foreground">{t("charts.noGraphSignals")}</div>;
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="rounded border px-2 py-1 font-mono">{symbol}</span>
        <SignalValue label={t("stockPred.rank")} value={String(latest.rank)} />
        <SignalValue label={t("stockPred.direction")} value={latest.direction} />
        <SignalValue label={t("stockPred.stage")} value={latest.stage} />
        <SignalValue label={t("stockPred.action")} value={latest.action} />
      </div>
      <div ref={ref} style={{ height }} />
    </section>
  );
}

function SignalValue({ label, value }: { label: string; value: string }) {
  return (
    <span className="rounded bg-muted px-2 py-1 text-muted-foreground">
      {label} {value || "-"}
    </span>
  );
}
