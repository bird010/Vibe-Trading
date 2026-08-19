import type { CandidateDecisionRow } from "../types";

interface Props {
  candidates: CandidateDecisionRow[];
  topN: number;
  primaryMetric: string;
  view: "changed" | "top" | "all";
  onInstrumentClick: (tsCode: string) => void;
}

export function RankingLane({ candidates, topN, primaryMetric, view, onInstrumentClick }: Props) {
  const ranked = candidates
    .filter((candidate) => candidate.stages.ranking_eligible)
    .sort((left, right) => (left.stages.rank ?? Infinity) - (right.stages.rank ?? Infinity));
  const values = ranked
    .map((candidate) => candidate.score?.value ?? candidate.primary_metric?.value)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  const minScore = values.length > 0 ? Math.min(...values) : 0;
  const maxScore = values.length > 0 ? Math.max(...values) : 1;
  const direction = ranked.find((candidate) => candidate.score?.direction)?.score?.direction ?? "HIGHER_BETTER";
  const visible = ranked.filter((candidate) => view === "all" || (view === "top" ? (candidate.stages.rank ?? Infinity) <= topN : (candidate.previous_weight ?? candidate.before_weight ?? 0) !== candidate.target_weight || (candidate.stages.rank ?? Infinity) <= topN + 2));
  const ineligibleDrops = candidates.filter((candidate) => {
    const before = candidate.before_weight ?? candidate.previous_weight ?? 0;
    return !candidate.stages.ranking_eligible && before > 0 && candidate.target_weight === 0;
  });
  return (
    <div className="space-y-2">
      <div className="flex justify-between text-xs text-muted-foreground"><span>{primaryMetric} Ranking</span><span>强 ← → 弱</span></div>
      <div className="relative space-y-1">
        {visible.map((candidate, index) => {
          const rank = candidate.stages.rank ?? 0;
          const before = candidate.before_weight ?? candidate.previous_weight ?? 0;
          const status = before === 0 && candidate.target_weight > 0 ? "NEW" : before > 0 && candidate.target_weight === 0 ? "DROP" : candidate.target_weight > 0 ? "KEEP" : rank > topN ? "MISSED CUTOFF" : "MISS";
          const score = candidate.score?.value ?? candidate.primary_metric?.value;
          const rawRatio = score == null || maxScore === minScore ? 1 : (score - minScore) / (maxScore - minScore);
          const ratio = direction === "LOWER_BETTER" ? 1 - rawRatio : rawRatio;
          const width = Math.max(3, Math.min(100, ratio * 100));
          const componentText = Object.entries(candidate.score?.components ?? {})
            .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
            .map(([name, value]) => `${name}: ${(value as number).toFixed(3)}`)
            .join(" · ");
          const row = <button key={candidate.ts_code} type="button" onClick={() => onInstrumentClick(candidate.ts_code)} className="grid w-full grid-cols-[3rem_8rem_minmax(6rem,1fr)_8rem] items-center gap-2 text-left text-xs"><span>#{rank || "—"}</span><span className="truncate font-mono">{candidate.ts_code}</span><span className="relative h-4 rounded bg-muted"><span className="absolute inset-y-0 left-0 rounded bg-blue-500/70" style={{ width: `${width}%` }} /></span><span className={status === "DROP" ? "text-red-700" : status === "NEW" ? "text-emerald-700" : "text-muted-foreground"}>{status} {score == null ? "" : score.toFixed(2)}{componentText && <span className="ml-1">· {componentText}</span>}</span></button>;
          return <span key={`${candidate.ts_code}-row`} className="block">{row}{index === topN - 1 && visible.length > topN && <div className="border-t border-dashed border-amber-500 pt-1 text-center text-[10px] text-amber-700">TOP {topN} CUTOFF</div>}</span>;
        })}
        {visible.length > 0 && visible.length <= topN && <div className="border-t border-dashed border-amber-500 pt-1 text-center text-[10px] text-amber-700">TOP {topN} CUTOFF</div>}
      </div>
      {ineligibleDrops.length > 0 && (
        <div className="space-y-1 border-t pt-2" aria-label="未入榜但已退出">
          <div className="text-[10px] text-muted-foreground">未进入 Ranking 的退出项</div>
          {ineligibleDrops.map((candidate) => {
            const score = candidate.score?.value ?? candidate.primary_metric?.value;
            return (
              <button key={`ineligible-${candidate.ts_code}`} type="button" onClick={() => onInstrumentClick(candidate.ts_code)} className="grid w-full grid-cols-[3rem_8rem_minmax(6rem,1fr)_8rem] items-center gap-2 text-left text-xs">
                <span>#—</span>
                <span className="truncate font-mono">{candidate.ts_code}</span>
                <span className="relative h-4 rounded bg-muted"><span className="absolute inset-y-0 left-0 rounded bg-slate-400/70" style={{ width: "3%" }} /></span>
                <span className="text-red-700">DROP · SCORE_INELIGIBLE {score == null ? "" : score.toFixed(2)}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
