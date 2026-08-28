---
name: optimizing-fund-rotation
description: Use when running multi-round AI research to improve Vibe-Trading fund-rotation strategies from an existing backtest, especially Champion-Challenger loops, Sol/Luna subagents, batch backtests, drawdown/return optimization, OOS controls, or forward-shadow qualification.
---

# Optimizing Fund Rotation

## Overview

Run a sequential, auditable Champion-Challenger experiment. The unit of progress is a falsifiable hypothesis that passes code, comparability, and validation gates—not a strategy that merely scores higher on a reused historical interval.

Before acting, read:

- [Experiment contract](references/experiment-contract.md) for data, metrics, state, and stopping rules.
- [Prompt templates](references/prompts.md) before dispatching analysis, implementation, review, or backtest work.

## Workflow

1. Treat the supplied run as an exploratory seed. Rebuild `round_00` on the eligible research-selection interval; do not use the seed's consumed interval as untouched OOS.
2. Freeze `experiment_spec.json`, fold manifest, snapshot/execution identities, metrics, and Champion gates before round 1.
3. Maintain an append-only ledger containing every success, failure, review, run ID, fold result, and Champion decision.
4. Execute exactly the authorized experiment budget. For the default campaign this is rounds `01..30`; a failed or non-improving round still consumes one round.
5. In each round:
   - dispatch one `gpt-5.6-luna` analyst for one primary hypothesis and a design;
   - dispatch one `gpt-5.6-luna` implementer to add a new strategy with tests;
   - dispatch a fresh `gpt-5.6-luna` reviewer;
   - return P0/P1 findings to the same implementer, then use a fresh reviewer, for at most five review cycles;
   - after tests and review pass, submit Champion and Challenger together through the strategy-batch API and wait for a verified terminal state;
   - replace the research Champion only when every pre-registered performance, identity, comparability, and implementation gate passes; deployment qualification remains separate.
6. Freeze the final candidate. A consumed confirmation run cannot change it. Pre-register at least 104 weeks of forward shadow before any deployment qualification claim.

## Hard Gates

- Add a new strategy ID; never alter an existing strategy implementation or its defaults.
- When registering a new strategy changes an exact catalog/registry regression assertion, update that assertion only to append the new strategy while preserving every existing strategy ID and invariant. Do not delete, weaken, broaden, or special-case the existing catalog contract.
- Stop a round before backtesting while P0/P1 findings or required tests remain.
- Do not change the public Runner, PIT/data contract, execution semantics, or evaluation policy without explicit approval.
- Do not rank incomparable, partial, corrupt, or quality-gate-failing runs. `RESEARCH_ONLY_UNVERIFIED_UNIVERSE` is an allowed research quality state: when Champion and Challenger share the same snapshot, universe, quality status, and publishable/comparability flags, it does not by itself block ranking or research-Champion promotion. Record the limitation and keep the campaign research-only.
- HTTP `202`, a process ID, or a disconnected SSE stream is not completion. Reuse the idempotency key and poll the batch ID to a terminal state.
- Do not add an extra round, silently shorten folds, consume the confirmation interval, delete failed trials, or relabel historical evidence as OOS.

## Completion

Report all rounds and the Champion path. The highest immediate status is `FROZEN_RESEARCH_CANDIDATE`; deployment qualification requires the pre-registered forward shadow.
