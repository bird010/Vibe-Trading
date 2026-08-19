import type { StrategyDecisionMetadata } from "../types";

export function StrategyPipeline({ strategy }: { strategy: StrategyDecisionMetadata }) {
  const scoreLabel = strategy.score_model?.label || "Strategy Score";
  const steps = [
    strategy.universe || "—",
    strategy.dedup_method || "—",
    strategy.representative_method || "—",
    `${scoreLabel} Ranking`,
    strategy.selection_rule || "—",
    strategy.weighting_rule || "—",
    strategy.rebalance_frequency || "—",
  ];
  return (
    <div className="flex flex-wrap items-center gap-1 rounded border bg-muted/10 p-3 text-xs">
      {steps.map((step, index) => <span key={`${step}-${index}`} className="inline-flex items-center gap-1"><span className="rounded bg-background px-2 py-1 shadow-sm">{step}</span>{index < steps.length - 1 && <span className="text-muted-foreground">↓</span>}</span>)}
    </div>
  );
}
