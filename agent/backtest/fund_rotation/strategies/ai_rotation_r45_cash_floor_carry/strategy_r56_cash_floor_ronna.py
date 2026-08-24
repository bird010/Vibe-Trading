"""Round 56: preserve a ronna cash floor after R39 carry."""
from __future__ import annotations
import math
from dataclasses import replace
from pydantic import BaseModel
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor, StrategyDataRequirements, StrategyDecisionContext, StrategyInitializationContext, TargetWeightDecision
from backtest.fund_rotation.strategies.ai_rotation_r34_staged_reentry.strategy import AiRotationR34StagedReentrySession, _append_reason
from backtest.fund_rotation.strategies.ai_rotation_r39_incumbent_carry.strategy import AiRotationR39IncumbentCarrySession, AiRotationR39IncumbentCarryStrategy
from backtest.fund_rotation.strategies.ai_rotation_r41_breadth_gated_carry.strategy import _classify_positive_targets, _r34_baseline, _r39_or_safe_baseline
CASH_FLOOR=1/6144
DESCRIPTOR=FundRotationStrategyDescriptor(id="ai_rotation_r56_cash_floor_ronna",name="承接释放权重保留六千一百四十四分之一现金持续几何动量相关性代表ETF",description="完全沿用 R39；仅当 carry 后现金低于 1/6144 时按原 carry 比例削减 carry，使现金回到 1/6144，其余情况逐值沿用 R39。",interface_version="1.0",supported_universe=("etf",),deterministic=True)
def apply_cash_floor_carry(previous_weights: object, staged_target_weights: object):
    t,c,s,i=_r39_or_safe_baseline(previous_weights,staged_target_weights)
    if c>=CASH_FLOOR:return t,c,s,i,bool(i)
    x=_classify_positive_targets(previous_weights,staged_target_weights)
    if x is None:return t,c,s,i,bool(i)
    staged,inc=x; base,_,_=_r34_baseline(previous_weights,staged_target_weights); carry={k:t.get(k,0)-base.get(k,0) for k in inc}; total=math.fsum(v for v in carry.values() if v>0); reduction=CASH_FLOOR-c
    if not staged or not inc or total<=0 or reduction<=0 or reduction>total+1e-9:return t,c,s,i,bool(i)
    out=dict(t)
    for k,v in carry.items():
        if v>0:out[k]-=reduction*v/total
    if not math.isfinite(math.fsum(out.values())):return t,c,s,i,bool(i)
    return out,CASH_FLOOR,s,i,True
class AiRotationR56CashFloorRonnaSession(AiRotationR39IncumbentCarrySession):
    def evaluate(self,context:StrategyDecisionContext)->TargetWeightDecision:
        prev=dict(self._previous_weights); d=AiRotationR34StagedReentrySession.evaluate(self,context); w,c,s,i,ap=apply_cash_floor_carry(prev,d.target_weights); diag=dict(d.diagnostics); diag.update({"staged_reentry_codes":sorted(s),"incumbent_carry_codes":sorted(i),"cash_floor":CASH_FLOOR,"cash_floor_rule":"preserve_one_six_thousand_one_hundred_forty_fourth_cash_after_r39_carry_only_when_floor_breached"}); d=replace(d,decision_id=f"{context.signal_date}-{DESCRIPTOR.id}",target_weights=w,cash_weight=c,reason_code=_append_reason(d.reason_code,"INCUMBENT_CARRY" if ap else ""),diagnostics=diag); self._patch_artifacts(d); return d
class AiRotationR56CashFloorRonnaStrategy(AiRotationR39IncumbentCarryStrategy):
    descriptor=DESCRIPTOR
    def describe_decision_pipeline(self,config:BaseModel)->dict[str,object]:
        p=super().describe_decision_pipeline(config);p["selection_rule"]+="; preserve a fixed 1/6144 cash floor after R39 carry only when breached";return p
    def resolve_requirements(self,config:BaseModel)->StrategyDataRequirements:return super().resolve_requirements(config)
    def create_session(self,initialization:StrategyInitializationContext,config:BaseModel)->AiRotationR56CashFloorRonnaSession:
        del initialization;return AiRotationR56CashFloorRonnaSession(config) # type: ignore[arg-type]

