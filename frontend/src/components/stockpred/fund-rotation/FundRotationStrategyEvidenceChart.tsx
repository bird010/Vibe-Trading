import { useEffect, useMemo, useState } from "react";
import type { StrategyEvidence, StrategyEvidencePoint, StrategyEvidenceSeries } from "./types";

interface Props {
  evidence?: StrategyEvidence | null;
  focusDate?: string;
  initialIndicator?: string | null;
  onIndicatorChange?: (indicatorId: string) => void;
}

function linePoints(points: StrategyEvidencePoint[], dates: string[], min: number, max: number): string {
  const values = new Map(points.map((point) => [point.date, point.value]));
  const range = max - min || 1;
  return dates
    .flatMap((date, index) => {
      const value = values.get(date);
      if (value === undefined) return [];
      const x = dates.length === 1 ? 320 : 24 + (index / (dates.length - 1)) * 592;
      const y = 148 - ((value - min) / range) * 124;
      return [`${x.toFixed(1)},${y.toFixed(1)}`];
    })
    .join(" ");
}

function activeSeries(evidence: StrategyEvidence | null | undefined, id: string | null): StrategyEvidenceSeries | null {
  return evidence?.indicators.find((series) => series.id === id) ?? evidence?.indicators[0] ?? null;
}

function scoreSeries(evidence: StrategyEvidence | null | undefined): StrategyEvidenceSeries | null {
  const score = evidence?.score;
  if (!score) return null;
  return {
    id: score.id,
    label: score.label,
    formula_id: `model.${score.model_id}`,
    unit: "score",
    points: score.points.map(({ date, value }) => ({ date, value })),
  };
}

export function FundRotationStrategyEvidenceChart({ evidence, focusDate, initialIndicator, onIndicatorChange }: Props) {
  const [activeId, setActiveId] = useState<string | null>(initialIndicator ?? evidence?.score?.id ?? evidence?.indicators[0]?.id ?? null);
  const strategyScore = scoreSeries(evidence);
  const series = strategyScore ?? activeSeries(evidence, activeId);

  useEffect(() => {
    if (!evidence?.score && !evidence?.indicators.some((indicator) => indicator.id === activeId)) {
      setActiveId(evidence?.indicators[0]?.id ?? null);
    }
  }, [activeId, evidence]);

  const chart = useMemo(() => {
    if (!series) return null;
    const benchmarkPoints = evidence?.benchmark?.normalized_price ?? [];
    const dates = Array.from(new Set([...series.points, ...benchmarkPoints].map((point) => point.date))).sort();
    const values = [...series.points, ...benchmarkPoints].map((point) => point.value).filter(Number.isFinite);
    const min = values.length ? Math.min(...values) : 0;
    const max = values.length ? Math.max(...values) : 1;
    return {
      dates,
      min,
      max,
      indicator: linePoints(series.points, dates, min, max),
      benchmark: linePoints(benchmarkPoints, dates, min, max),
    };
  }, [evidence, series]);

  if (!evidence || (!strategyScore && evidence.indicators.length === 0) || !series || !chart) {
    return <div className="rounded border bg-muted/20 px-3 py-6 text-center text-sm text-muted-foreground">策略证据暂无，当前运行未保存策略指标时间序列。</div>;
  }

  return (
    <section className="space-y-2 rounded border p-3" data-testid="strategy-evidence-chart">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold">策略证据</h4>
        <div className="flex flex-wrap gap-1">
          {(strategyScore ? [strategyScore] : evidence.indicators).map((indicator) => (
            <button
              key={indicator.id}
              type="button"
              className={`rounded border px-2 py-1 text-xs ${indicator.id === series.id ? "border-primary bg-primary/10" : ""}`}
              aria-pressed={indicator.id === series.id}
              onClick={() => {
                setActiveId(indicator.id);
                onIndicatorChange?.(indicator.id);
              }}
            >
              {indicator.label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span>{series.label} · {series.formula_id} · {series.unit}</span>
        {evidence.benchmark && <span>基准：{[evidence.benchmark.ts_code, evidence.benchmark.name].filter(Boolean).join(" · ")}</span>}
      </div>
      <div className="overflow-x-auto rounded bg-muted/10 p-1">
        <svg viewBox="0 0 640 180" className="h-[180px] min-w-[520px] w-full" role="img" aria-label={`${series.label} 策略证据时间序列`}>
          <line x1="24" y1="148" x2="616" y2="148" stroke="currentColor" strokeOpacity="0.15" />
          <line x1="24" y1="24" x2="24" y2="148" stroke="currentColor" strokeOpacity="0.15" />
          {chart.benchmark && <polyline points={chart.benchmark} fill="none" stroke="currentColor" strokeDasharray="4 4" strokeOpacity="0.45" strokeWidth="1.5" />}
          <polyline points={chart.indicator} fill="none" stroke="hsl(var(--primary))" strokeWidth="2" />
          {focusDate && chart.dates.includes(focusDate) && (
            <line x1={(24 + (chart.dates.indexOf(focusDate) / Math.max(chart.dates.length - 1, 1)) * 592).toFixed(1)} y1="18" x2={(24 + (chart.dates.indexOf(focusDate) / Math.max(chart.dates.length - 1, 1)) * 592).toFixed(1)} y2="154" stroke="hsl(var(--destructive))" strokeDasharray="3 3" />
          )}
          <text x="24" y="171" fontSize="9" fill="currentColor" opacity="0.6">{chart.dates[0]}</text>
          <text x="616" y="171" textAnchor="end" fontSize="9" fill="currentColor" opacity="0.6">{chart.dates[chart.dates.length - 1]}</text>
        </svg>
      </div>
    </section>
  );
}
