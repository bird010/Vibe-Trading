from __future__ import annotations

from datetime import datetime, timezone

import pytest

import backtest.fund_rotation.oos_validation as oos_validation
from backtest.fund_rotation.oos_validation import (
    COMPONENT_CHAIN_STAGES,
    BenchmarkPolicy,
    OOSConsumptionLedger,
    QualificationPolicy,
    RollingWalkForwardPolicy,
    SelectionPolicy,
    TemporalSplitPolicy,
    VariantSpec,
    WalkForwardAccountState,
    build_component_chain_metadata,
    build_oos_evidence_table,
    create_research_experiment,
    rank_validation_candidates,
    run_walk_forward_state_chain,
)


def _weeks(count: int) -> tuple[str, ...]:
    return tuple(f"w{i:03d}" for i in range(count))


def _variant(
    key: str,
    *,
    parameters: dict[str, object] | None = None,
    stage: str = "S1",
    uses_clustering: bool = False,
    universe_id: str = "cn_equity_etf",
    execution_contract_version: str = "exec-v1",
) -> VariantSpec:
    return VariantSpec(
        variant_key=key,
        strategy_family="etf_absolute_momentum",
        parameters=parameters or {"lookback_weeks": 12},
        component_stage=stage,
        component_toggles={"momentum_family": "12M"},
        uses_clustering=uses_clustering,
        universe_id=universe_id,
        execution_contract_version=execution_contract_version,
        data_identity_hash="data-v1",
        code_identity_hash="code-v1",
        evaluation_identity_hash="eval-v1",
    )


def _experiment(*variants: VariantSpec, parameter_space: dict[str, object] | None = None):
    weeks = _weeks(320)
    return create_research_experiment(
        hypothesis="direct ETF momentum has persistent OOS edge",
        primary_metric="sharpe",
        secondary_metrics=("max_drawdown", "turnover"),
        parameter_space=parameter_space or {"lookback_weeks": [12]},
        selection_policy=SelectionPolicy(primary_metric="sharpe"),
        split_policy=TemporalSplitPolicy(
            train_weeks=weeks[:160],
            validation_weeks=weeks[160:216],
            oos_weeks=weeks[216:],
        ),
        benchmark_policy=BenchmarkPolicy(
            primary_benchmark="000300.SH",
            secondary_benchmarks=("510300.SH",),
            cash_benchmark="cash_cny",
            universe_equal_weight_benchmark="cn_equity_etf_equal_weight",
            benchmark_data_version="bench-v1",
        ),
        qualification_policy=QualificationPolicy(),
        candidate_variants=variants or (_variant("absolute-12m"),),
    )


def _qualified_experiment_kwargs(
    *,
    variants: tuple[VariantSpec, ...] | None = None,
    parameter_space: dict[str, object] | None = None,
    walk_forward_policy: RollingWalkForwardPolicy | None = None,
) -> dict[str, object]:
    weeks = _weeks(320)
    kwargs: dict[str, object] = {
        "hypothesis": "direct ETF momentum has persistent OOS edge",
        "primary_metric": "sharpe",
        "secondary_metrics": ("max_drawdown", "turnover"),
        "parameter_space": parameter_space or {"lookback_weeks": [12]},
        "selection_policy": SelectionPolicy(primary_metric="sharpe"),
        "split_policy": TemporalSplitPolicy(
            train_weeks=weeks[:160],
            validation_weeks=weeks[160:216],
            oos_weeks=weeks[216:],
        ),
        "benchmark_policy": BenchmarkPolicy(
            primary_benchmark="000300.SH",
            secondary_benchmarks=("510300.SH",),
            cash_benchmark="cash_cny",
            universe_equal_weight_benchmark="cn_equity_etf_equal_weight",
            benchmark_data_version="bench-v1",
        ),
        "qualification_policy": QualificationPolicy(),
        "candidate_variants": variants or (_variant("absolute-12m"),),
    }
    if walk_forward_policy is not None:
        kwargs["walk_forward_policy"] = walk_forward_policy
    return kwargs


def _formal_non_cluster_baseline(experiment) -> dict[str, object]:
    return {
        "baseline_id": "independent-alpha-baseline-v1",
        "strategy_family": "independent_alpha_baseline",
        "uses_clustering": False,
        "oos_qualification_label": "QUALIFIED_OOS_EVIDENCE",
        "strategy_implementation_hash": "code-v1",
        "framework_implementation_hash": "framework-default-v1",
        "resolved_config_hash": oos_validation.canonical_identity_hash({"lookback_weeks": 12}),
        "knowledge_cutoff": "knowledge-cutoff-default-v1",
        "universe_id": "cn_equity_etf",
        "execution_contract_version": "exec-v1",
        "execution_policy_hash": oos_validation.canonical_identity_hash("exec-v1"),
        "accounting_contract_version": "daily_accounting_v1",
        "accounting_policy_hash": oos_validation.canonical_identity_hash("daily_accounting_v1"),
        "market_rule_policy_hash": oos_validation.canonical_identity_hash("market-rule-default-v1"),
        "evaluation_calendar_hash": oos_validation.canonical_identity_hash(tuple(experiment.split_policy.oos_weeks)),
        "benchmark_policy_hash": oos_validation.canonical_identity_hash(experiment.benchmark_policy),
        "qualification_policy_hash": experiment.qualification_policy_hash,
        "data_snapshot_fingerprint": "data-v1",
        "research_experiment_id": experiment.experiment_id,
    }


def _baseline_with_camel_case_contract_fields(experiment) -> dict[str, object]:
    baseline = _formal_non_cluster_baseline(experiment)
    return {
        "baselineId": baseline["baseline_id"],
        "strategyFamily": "etf_absolute_momentum",
        "usesClustering": baseline["uses_clustering"],
        "oosQualificationLabel": baseline["oos_qualification_label"],
        "strategyImplementationHash": baseline["strategy_implementation_hash"],
        "frameworkImplementationHash": baseline["framework_implementation_hash"],
        "resolvedConfigHash": baseline["resolved_config_hash"],
        "knowledgeCutoff": baseline["knowledge_cutoff"],
        "universeId": baseline["universe_id"],
        "executionContractVersion": baseline["execution_contract_version"],
        "executionPolicyHash": baseline["execution_policy_hash"],
        "accountingContractVersion": baseline["accounting_contract_version"],
        "accountingPolicyHash": baseline["accounting_policy_hash"],
        "marketRulePolicyHash": baseline["market_rule_policy_hash"],
        "evaluationCalendarHash": baseline["evaluation_calendar_hash"],
        "benchmarkPolicyHash": baseline["benchmark_policy_hash"],
        "qualificationPolicyHash": baseline["qualification_policy_hash"],
        "dataSnapshotFingerprint": baseline["data_snapshot_fingerprint"],
        "researchExperimentId": baseline["research_experiment_id"],
    }


def _baseline_with_compact_lowercase_contract_fields(experiment) -> dict[str, object]:
    baseline = _formal_non_cluster_baseline(experiment)
    return {key.replace("_", ""): value for key, value in baseline.items()}


def test_temporal_split_rejects_overlap_and_refuses_unqualified_oos_evidence() -> None:
    weeks = _weeks(280)

    with pytest.raises(ValueError, match="overlap"):
        TemporalSplitPolicy(
            train_weeks=weeks[:156],
            validation_weeks=weeks[150:208],
            oos_weeks=weeks[208:280],
        )

    split = TemporalSplitPolicy(
        train_weeks=weeks[:156],
        validation_weeks=weeks[156:208],
        oos_weeks=weeks[208:280],
    )

    assert split.oos_qualification_label() == "RESEARCH_ONLY"
    with pytest.raises(ValueError, match="QUALIFIED_OOS_EVIDENCE"):
        split.require_qualified_oos_evidence()

    long_weeks = _weeks(620)
    long_sample_with_small_oos_fraction = TemporalSplitPolicy(
        train_weeks=long_weeks[:400],
        validation_weeks=long_weeks[400:516],
        oos_weeks=long_weeks[516:620],
    )
    assert long_sample_with_small_oos_fraction.oos_qualification_label() == "RESEARCH_ONLY"


def test_qualified_oos_evidence_requires_completed_non_cluster_baseline_contract() -> None:
    experiment = create_research_experiment(**_qualified_experiment_kwargs())
    policy = QualificationPolicy(require_non_cluster_baseline=True)

    with pytest.raises(ValueError, match="non-cluster baseline"):
        oos_validation.require_qualified_oos_evidence(
            experiment,
            qualification_policy=policy,
            non_cluster_baseline_results=(),
        )

    with pytest.raises(ValueError, match="same OOS contract"):
        oos_validation.require_qualified_oos_evidence(
            experiment,
            qualification_policy=policy,
            non_cluster_baseline_results=(
                {
                    "variant_key": "cluster-family",
                    "uses_clustering": True,
                    "oos_qualification_label": "QUALIFIED_OOS_EVIDENCE",
                    "execution_contract_version": "exec-v1",
                    "oos_weeks": tuple(experiment.split_policy.oos_weeks),
                },
            ),
        )

    label = oos_validation.require_qualified_oos_evidence(
        experiment,
        qualification_policy=policy,
        non_cluster_baseline_results=(_formal_non_cluster_baseline(experiment),),
    )

    assert label == "QUALIFIED_OOS_EVIDENCE"


def test_oos_accounting_contract_defaults_match_daily_accounting_v1() -> None:
    """Catches OOS defaulting to the legacy accounting-v1 contract while attribution uses daily accounting."""
    variant = _variant("absolute-12m")
    state = WalkForwardAccountState(
        cash=1000.0,
        positions={"510300.SH": 10.0},
        cost_basis={"510300.SH": 4.5},
        residual_orders=(),
        corporate_action_state={"510300.SH": "none"},
        last_valuation_date="w207",
        last_nav=1.0,
    )

    assert variant.accounting_policy_hash == oos_validation.canonical_identity_hash("daily_accounting_v1")
    assert state.accounting_contract_version == "daily_accounting_v1"


def test_oos_qualification_policy_spec_is_canonical_name_with_legacy_alias() -> None:
    """Catches the old QualificationPolicy class name leaking into policy identity/debug surfaces."""
    assert oos_validation.QualificationPolicy is oos_validation.OOSQualificationPolicySpec
    assert oos_validation.QualificationPolicy().__class__.__name__ == "OOSQualificationPolicySpec"


def test_qualified_oos_evidence_accepts_canonical_baseline_contract_field_aliases() -> None:
    experiment = create_research_experiment(**_qualified_experiment_kwargs())

    label = oos_validation.require_qualified_oos_evidence(
        experiment,
        qualification_policy=QualificationPolicy(require_non_cluster_baseline=True),
        non_cluster_baseline_results=(_baseline_with_camel_case_contract_fields(experiment),),
    )

    assert label == "QUALIFIED_OOS_EVIDENCE"


def test_qualified_oos_evidence_accepts_compact_lowercase_baseline_contract_field_aliases() -> None:
    experiment = create_research_experiment(**_qualified_experiment_kwargs())

    label = oos_validation.require_qualified_oos_evidence(
        experiment,
        qualification_policy=QualificationPolicy(require_non_cluster_baseline=True),
        non_cluster_baseline_results=(_baseline_with_compact_lowercase_contract_fields(experiment),),
    )

    assert label == "QUALIFIED_OOS_EVIDENCE"


def test_qualified_oos_evidence_rejects_conflicting_baseline_contract_aliases() -> None:
    experiment = create_research_experiment(**_qualified_experiment_kwargs())
    baseline = _formal_non_cluster_baseline(experiment)
    baseline["strategyImplementationHash"] = "different-strategy-hash"

    with pytest.raises(ValueError, match="conflicting.*strategy_implementation_hash"):
        oos_validation.require_qualified_oos_evidence(
            experiment,
            qualification_policy=QualificationPolicy(require_non_cluster_baseline=True),
            non_cluster_baseline_results=(baseline,),
        )


def test_qualified_oos_evidence_allows_baseline_own_strategy_framework_and_config_hashes() -> None:
    """Catches formal baseline implementation hashes being mistaken for common OOS comparison contract fields."""
    experiment = create_research_experiment(**_qualified_experiment_kwargs())
    baseline = _formal_non_cluster_baseline(experiment)
    baseline["strategy_implementation_hash"] = "baseline-strategy-v2"
    baseline["framework_implementation_hash"] = "baseline-framework-v2"
    baseline["resolved_config_hash"] = "baseline-config-v2"

    label = oos_validation.require_qualified_oos_evidence(
        experiment,
        qualification_policy=QualificationPolicy(require_non_cluster_baseline=True),
        non_cluster_baseline_results=(baseline,),
    )

    assert label == "QUALIFIED_OOS_EVIDENCE"


@pytest.mark.parametrize(
    "strategy_family",
    (
        "etf_absolute_momentum",
        "etf_dual_momentum",
        "etf_trend_momentum",
    ),
)
def test_qualified_oos_evidence_accepts_designed_non_cluster_baseline_families(strategy_family: str) -> None:
    experiment = create_research_experiment(**_qualified_experiment_kwargs())
    baseline = _formal_non_cluster_baseline(experiment)
    baseline["strategy_family"] = strategy_family

    label = oos_validation.require_qualified_oos_evidence(
        experiment,
        qualification_policy=QualificationPolicy(require_non_cluster_baseline=True),
        non_cluster_baseline_results=(baseline,),
    )

    assert label == "QUALIFIED_OOS_EVIDENCE"


@pytest.mark.parametrize(
    "missing_key",
    (
        "baseline_id",
        "strategy_family",
        "uses_clustering",
        "oos_qualification_label",
        "strategy_implementation_hash",
        "framework_implementation_hash",
        "resolved_config_hash",
        "knowledge_cutoff",
        "universe_id",
        "execution_contract_version",
        "execution_policy_hash",
        "accounting_contract_version",
        "accounting_policy_hash",
        "market_rule_policy_hash",
        "evaluation_calendar_hash",
        "benchmark_policy_hash",
        "qualification_policy_hash",
        "data_snapshot_fingerprint",
        "research_experiment_id",
    ),
)
def test_qualified_oos_evidence_rejects_baseline_missing_any_formal_contract_field(missing_key: str) -> None:
    experiment = create_research_experiment(**_qualified_experiment_kwargs())
    baseline = _formal_non_cluster_baseline(experiment)
    del baseline[missing_key]

    with pytest.raises(ValueError, match="non-cluster baseline.*same OOS contract"):
        oos_validation.require_qualified_oos_evidence(
            experiment,
            qualification_policy=QualificationPolicy(require_non_cluster_baseline=True),
            non_cluster_baseline_results=(baseline,),
        )


@pytest.mark.parametrize(
    "field,bad_value",
    (
        ("strategy_family", "unknown_alpha_baseline"),
        ("uses_clustering", True),
        ("oos_qualification_label", "RESEARCH_ONLY"),
        ("knowledge_cutoff", "2026-08-12T15:00:00"),
        ("universe_id", "different_universe"),
        ("execution_contract_version", "exec-v2"),
        ("execution_policy_hash", "different-execution-policy"),
        ("accounting_contract_version", "accounting-v2"),
        ("accounting_policy_hash", "different-accounting-policy"),
        ("market_rule_policy_hash", "different-market-rule-policy"),
        ("evaluation_calendar_hash", "different-calendar"),
        ("benchmark_policy_hash", "different-benchmark"),
        ("qualification_policy_hash", "different-qualification"),
        ("data_snapshot_fingerprint", "different-data"),
        ("research_experiment_id", "different-experiment"),
    ),
)
def test_qualified_oos_evidence_rejects_baseline_contract_identity_mismatch(field: str, bad_value: object) -> None:
    experiment = create_research_experiment(**_qualified_experiment_kwargs())
    baseline = _formal_non_cluster_baseline(experiment)
    baseline[field] = bad_value

    with pytest.raises(ValueError, match="non-cluster baseline.*same OOS contract"):
        oos_validation.require_qualified_oos_evidence(
            experiment,
            qualification_policy=QualificationPolicy(require_non_cluster_baseline=True),
            non_cluster_baseline_results=(baseline,),
        )


def test_rolling_walk_forward_generates_default_folds_and_preserves_account_state() -> None:
    weeks = _weeks(364)
    folds = RollingWalkForwardPolicy().generate_folds(weeks)
    initial_state = WalkForwardAccountState(
        cash=1000.0,
        positions={"510300.SH": 10.0},
        cost_basis={"510300.SH": 4.5},
        residual_orders=("buy-512880",),
        corporate_action_state={"510300.SH": "none"},
        last_valuation_date="w207",
        last_nav=1.0,
    )

    assert [(len(f.train_weeks), len(f.validation_weeks), len(f.test_weeks)) for f in folds] == [
        (156, 52, 52),
        (156, 52, 52),
        (156, 52, 52),
    ]
    assert folds[0].test_weeks[0] == "w208"
    assert folds[1].test_weeks[0] == "w260"
    with pytest.raises(TypeError):
        initial_state.positions["510300.SH"] = 99.0

    def transition(fold, state: WalkForwardAccountState) -> WalkForwardAccountState:
        return state.replace(
            cash=state.cash + 10.0,
            residual_orders=state.residual_orders + (f"fold-{fold.fold_index}-carry",),
            last_valuation_date=fold.test_weeks[-1],
            last_nav=state.last_nav + 0.01,
        )

    runs = run_walk_forward_state_chain(folds, initial_state, transition)

    assert runs[1].start_state == runs[0].end_state
    assert runs[2].start_state == runs[1].end_state
    assert runs[-1].end_state.cash == 1030.0
    assert runs[-1].end_state.residual_orders[-1] == "fold-2-carry"


def test_validation_ranking_ignores_oos_and_oos_evidence_has_no_winner_or_rank() -> None:
    variants = (_variant("strong-validation"), _variant("strong-oos"))
    ranking = rank_validation_candidates(
        [
            {
                "variant_key": "strong-validation",
                "sharpe": 1.5,
                "max_drawdown": -0.2,
                "turnover": 0.3,
            },
            {
                "variant_key": "strong-oos",
                "sharpe": 0.1,
                "max_drawdown": -0.1,
                "turnover": 0.1,
            },
        ],
        SelectionPolicy(primary_metric="sharpe"),
    )

    assert ranking[0]["variant_key"] == "strong-validation"
    assert ranking[0]["selection_rank"] == 1
    with pytest.raises(ValueError, match="OOS"):
        rank_validation_candidates([], SelectionPolicy(primary_metric="oos_sharpe"))
    with pytest.raises(ValueError, match="sealed"):
        rank_validation_candidates(
            [
                {
                    "variant_key": "strong-validation",
                    "sharpe": 1.5,
                    "oos_sharpe": -1.0,
                },
            ],
            SelectionPolicy(primary_metric="sharpe"),
        )

    evidence = build_oos_evidence_table(
        _experiment(*variants),
        {
            "strong-validation": {"oos_sharpe": -1.0, "qualified": False},
            "strong-oos": {"oos_sharpe": 3.0, "qualified": True},
        },
    )

    assert [row["variant_key"] for row in evidence] == ["strong-validation", "strong-oos"]
    assert all("winner" not in row and "selection_rank" not in row and "rank" not in row for row in evidence)
    with pytest.raises(ValueError, match="rank"):
        build_oos_evidence_table(
            _experiment(*variants),
            {"strong-validation": {"oos_sharpe": -1.0, "selection_rank": 1}},
        )


@pytest.mark.parametrize(
    "forbidden_field",
    (
        "rank_score",
        "rankScore",
        "rankscore",
        "selection_score",
        "selectionRank",
        "selectionrank",
        "selected_winner",
        "selectedwinner",
        "winner_name",
        "winnerName",
        "winnername",
        "选优",
        "排名",
        "选优排名",
    ),
)
def test_oos_evidence_table_rejects_winner_rank_selection_synonyms(forbidden_field: str) -> None:
    with pytest.raises(ValueError, match="winner|rank|selection|选优|排名"):
        build_oos_evidence_table(
            _experiment(_variant("absolute-12m")),
            {"absolute-12m": {"oos_sharpe": 1.0, forbidden_field: "leaky-selection-signal"}},
        )


def test_selection_policy_rejects_unknown_or_sealed_metrics_even_when_registered() -> None:
    with pytest.raises(ValueError, match="unknown"):
        SelectionPolicy(primary_metric="sortino")

    with pytest.raises(ValueError, match="sealed"):
        SelectionPolicy(primary_metric="oos_sharpe")

    custom_policy = SelectionPolicy(
        primary_metric="sortino",
        allowed_validation_metrics=("sharpe", "sortino"),
    )

    with pytest.raises(ValueError, match="sealed"):
        rank_validation_candidates(
            [
                {
                    "variant_key": "renamed-oos",
                    "sortino": 1.2,
                    "oos_proxy": 3.0,
                }
            ],
            custom_policy,
        )

    with pytest.raises(ValueError, match="unknown"):
        rank_validation_candidates(
            [
                {
                    "variant_key": "unknown-field",
                    "sortino": 1.2,
                    "unregistered_metric": 3.0,
                }
            ],
            custom_policy,
        )


@pytest.mark.parametrize("metric", ("proxySharpe", "proxy_sharpe", "proxysharpe"))
def test_selection_policy_rejects_proxy_metrics_even_when_registered(metric: str) -> None:
    with pytest.raises(ValueError, match="proxy"):
        SelectionPolicy(
            primary_metric=metric,
            allowed_validation_metrics=("sharpe", metric),
        )


@pytest.mark.parametrize(
    "metric",
    (
        "oosProxy",
        "validation_sharpe",
        "validationProxy",
        "validationOutOfSample",
        "validation_post_hoc_sharpe",
        "validationPostHocSharpe",
        "validation_posthoc_sharpe",
        "validation-post-hoc-sharpe",
        "validation post hoc sharpe",
        "outOfSampleSharpe",
        "out_of_sample_sharpe",
        "out-of-sample-sharpe",
        "validation_oos_sharpe",
        "validation_test_sharpe",
        "validationTestSharpe",
        "holdout_sharpe",
        "holdoutSharpe",
        "validation_untouched_sharpe",
        "validation_sealed_sharpe",
        "sealedSharpe",
        "validation_regime_sharpe",
        "regimeSharpe",
    ),
)
def test_validation_post_hoc_metric_semantics_are_rejected_even_when_registered(metric: str) -> None:
    with pytest.raises(ValueError, match="sealed"):
        SelectionPolicy(
            primary_metric=metric,
            allowed_validation_metrics=("sharpe", metric),
        )


@pytest.mark.parametrize(
    "metric",
    (
        "oosProxy",
        "oosproxy",
        "validationProxy",
        "validationOutOfSample",
        "validationPostHocProxy",
        "sealedProxy",
        "holdoutProxy",
        "regimeProxy",
    ),
)
def test_selection_policy_rejects_sealed_metric_semantics_at_registration(metric: str) -> None:
    with pytest.raises(ValueError, match="sealed"):
        SelectionPolicy(
            primary_metric="sharpe",
            allowed_validation_metrics=("sharpe", metric),
        )


@pytest.mark.parametrize(
    "metric",
    (
        "oosProxy",
        "validation_proxy",
        "validationProxy",
        "validation_post_hoc_proxy",
        "validationPostHocProxy",
        "validation-untouched-proxy",
        "validation sealed proxy",
        "validationSealedProxy",
        "out_of_sample_proxy",
        "outOfSampleProxy",
        "holdout_proxy",
        "holdoutProxy",
        "validation_regime_proxy",
        "validationRegimeProxy",
    ),
)
def test_validation_post_hoc_result_fields_are_rejected_before_unknown_metric_handling(metric: str) -> None:
    with pytest.raises(ValueError, match="sealed"):
        rank_validation_candidates(
            [
                {
                    "variant_key": "leaky-field",
                    "sharpe": 1.2,
                    metric: 2.0,
                }
            ],
            SelectionPolicy(primary_metric="sharpe"),
        )


def test_validation_proxy_result_fields_are_rejected_before_unknown_metric_handling() -> None:
    with pytest.raises(ValueError, match="proxy"):
        rank_validation_candidates(
            [
                {
                    "variant_key": "leaky-field",
                    "sharpe": 1.2,
                    "proxySharpe": 2.0,
                }
            ],
            SelectionPolicy(primary_metric="sharpe"),
        )


def test_oos_consumption_ledger_records_research_input_lineage() -> None:
    consumed_at = datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc)
    ledger = OOSConsumptionLedger()

    updated = ledger.consume(
        source_experiment_id="exp-source",
        consumed_at=consumed_at,
        derived_experiment_ids=("exp-derived-a", "exp-derived-b"),
    )

    assert updated.entries == (
        {
            "source_experiment_id": "exp-source",
            "status": "CONSUMED_AS_RESEARCH_INPUT",
            "consumed_at": "2026-08-12T09:30:00+00:00",
            "derived_experiment_ids": ("exp-derived-a", "exp-derived-b"),
        },
    )
    assert ledger.entries == ()


def test_oos_evidence_and_consumption_entries_are_deep_immutable() -> None:
    evidence = build_oos_evidence_table(
        _experiment(_variant("absolute-12m")),
        {"absolute-12m": {"oos_sharpe": 1.1, "diagnostics": {"folds": ["f1"]}}},
    )

    with pytest.raises(TypeError):
        evidence[0]["oos_sharpe"] = 2.2
    with pytest.raises(TypeError):
        evidence[0]["diagnostics"]["new_key"] = "drift"
    with pytest.raises(AttributeError):
        evidence[0]["diagnostics"]["folds"].append("f2")

    ledger = OOSConsumptionLedger().consume(
        source_experiment_id="exp-source",
        consumed_at=datetime(2026, 8, 12, 9, 30, tzinfo=timezone.utc),
        derived_experiment_ids=("exp-derived",),
    )

    with pytest.raises(TypeError):
        ledger.entries[0]["status"] = "REWRITTEN"
    with pytest.raises(AttributeError):
        ledger.entries[0]["derived_experiment_ids"].append("exp-drift")


def test_experiment_identity_changes_with_parameter_space_or_candidate_identity() -> None:
    base_variant = _variant("absolute-12m", parameters={"lookback_weeks": 12})
    base = _experiment(base_variant, parameter_space={"lookback_weeks": [12]})

    changed_space = _experiment(base_variant, parameter_space={"lookback_weeks": [12, 26]})
    changed_candidate = _experiment(
        _variant("absolute-26w", parameters={"lookback_weeks": 26}),
        parameter_space={"lookback_weeks": [12]},
    )

    assert base.experiment_id != changed_space.experiment_id
    assert base.experiment_id != changed_candidate.experiment_id
    assert base.sealed_candidate_identity_hashes != changed_candidate.sealed_candidate_identity_hashes


def test_identity_inputs_are_deep_frozen_and_identity_verification_catches_drift() -> None:
    parameter_space = {"lookback_weeks": [12], "filters": {"risk": ["low"]}}
    parameters = {"lookback_weeks": [12], "risk": {"enabled": True}}
    component_toggles = {"momentum_family": "12M", "weighting": {"scheme": "equal"}}
    variant = _variant(
        "absolute-12m",
        parameters=parameters,
        stage="S5",
    )
    object.__setattr__(variant, "component_toggles", oos_validation.freeze_for_identity(component_toggles))
    experiment = create_research_experiment(
        **_qualified_experiment_kwargs(variants=(variant,), parameter_space=parameter_space)
    )

    with pytest.raises(AttributeError):
        experiment.parameter_space["lookback_weeks"].append(26)
    with pytest.raises(TypeError):
        experiment.parameter_space["filters"]["risk"] = ("low", "medium")
    with pytest.raises(AttributeError):
        variant.parameters["lookback_weeks"].append(26)
    with pytest.raises(TypeError):
        variant.parameters["risk"]["enabled"] = False
    with pytest.raises(TypeError):
        variant.component_toggles["weighting"]["scheme"] = "risk_parity"

    original_hash = oos_validation.canonical_identity_hash({"lookback_weeks": [12]})
    with pytest.raises(ValueError, match="identity drift"):
        oos_validation.assert_identity_matches("parameter_space", {"lookback_weeks": [12, 26]}, original_hash)

    with pytest.raises(ValueError, match="experiment_id"):
        oos_validation.ResearchExperiment(
            experiment_id="not-the-canonical-id",
            hypothesis=experiment.hypothesis,
            primary_metric=experiment.primary_metric,
            secondary_metrics=experiment.secondary_metrics,
            parameter_space=experiment.parameter_space,
            selection_policy=experiment.selection_policy,
            split_policy=experiment.split_policy,
            benchmark_policy=experiment.benchmark_policy,
            qualification_policy_hash=experiment.qualification_policy_hash,
            candidate_variants=experiment.candidate_variants,
            sealed_candidate_identity_hashes=experiment.sealed_candidate_identity_hashes,
            walk_forward_policy=experiment.walk_forward_policy,
        )


def test_walk_forward_policy_participates_in_experiment_identity() -> None:
    base = create_research_experiment(
        **_qualified_experiment_kwargs(walk_forward_policy=RollingWalkForwardPolicy(step_weeks=52))
    )
    changed_window = create_research_experiment(
        **_qualified_experiment_kwargs(walk_forward_policy=RollingWalkForwardPolicy(step_weeks=26))
    )

    assert base.experiment_id != changed_window.experiment_id


def test_component_chain_is_fixed_and_non_cluster_baselines_are_declared() -> None:
    metadata = build_component_chain_metadata(
        universe_id="cn_equity_etf",
        execution_contract_version="exec-v1",
    )

    assert tuple(stage.stage_id for stage in metadata.component_chain) == COMPONENT_CHAIN_STAGES
    assert metadata.execution_ladder_included is False
    assert all(stage.universe_id == "cn_equity_etf" for stage in metadata.component_chain)
    assert all(stage.execution_contract_version == "exec-v1" for stage in metadata.component_chain)
    assert {baseline.strategy_family for baseline in metadata.non_cluster_baselines} == {
        "etf_absolute_momentum",
        "etf_dual_momentum",
        "etf_trend_momentum",
    }
    assert all(baseline.uses_clustering is False for baseline in metadata.non_cluster_baselines)


def test_component_chain_variants_require_fixed_order_and_single_registered_toggle_diffs() -> None:
    base_toggles = {
        "momentum": "direct",
        "clustering": False,
        "representative_etf": True,
        "quality": True,
        "weighting_model": "equal",
        "risk_overlay": "none",
    }

    def make_valid_chain() -> tuple[VariantSpec, ...]:
        chain = tuple(
            _variant(
                f"stage-{stage}",
                stage=stage,
                uses_clustering=stage not in {"S0", "S1"},
                parameters={"lookback_weeks": 12},
            )
            for stage in COMPONENT_CHAIN_STAGES
        )
        for variant, stage in zip(chain, COMPONENT_CHAIN_STAGES, strict=True):
            stage_toggles = dict(base_toggles)
            if stage == "S0":
                stage_toggles = {"clustering": False}
            elif stage == "S1":
                stage_toggles = {"clustering": False, "momentum": "direct"}
            elif stage == "S2":
                stage_toggles = {"momentum": "direct", "clustering": True}
            elif stage == "S3":
                stage_toggles = {"momentum": "direct", "clustering": True, "representative_etf": True}
            elif stage == "S4":
                stage_toggles = {
                    "momentum": "direct",
                    "clustering": True,
                    "representative_etf": True,
                    "quality": True,
                }
            elif stage == "S5":
                stage_toggles = {
                    "momentum": "direct",
                    "clustering": True,
                    "representative_etf": True,
                    "quality": True,
                    "weighting_model": "equal",
                }
            elif stage == "S6":
                stage_toggles = {
                    "momentum": "direct",
                    "clustering": True,
                    "representative_etf": True,
                    "quality": True,
                    "weighting_model": "equal",
                    "risk_overlay": "target_vol",
                }
            object.__setattr__(variant, "component_toggles", oos_validation.freeze_for_identity(stage_toggles))
            object.__setattr__(variant, "accounting_policy_hash", "accounting-policy-v1")
            object.__setattr__(variant, "evaluation_calendar_hash", "evaluation-calendar-v1")
            object.__setattr__(variant, "benchmark_policy_hash", "benchmark-policy-v1")
        return chain

    valid_chain = make_valid_chain()

    oos_validation.validate_component_chain_variants(valid_chain)

    with pytest.raises(ValueError, match="S0.*S6"):
        oos_validation.validate_component_chain_variants(valid_chain[1:] + valid_chain[:1])

    bad_weighting_boundary = list(make_valid_chain())
    object.__setattr__(
        bad_weighting_boundary[5],
        "component_toggles",
        oos_validation.freeze_for_identity({**dict(valid_chain[4].component_toggles), "risk_overlay": "target_vol"}),
    )
    with pytest.raises(ValueError, match="S4/S5.*weighting"):
        oos_validation.validate_component_chain_variants(tuple(bad_weighting_boundary))

    bad_risk_boundary = list(make_valid_chain())
    object.__setattr__(
        bad_risk_boundary[6],
        "component_toggles",
        oos_validation.freeze_for_identity({**dict(valid_chain[5].component_toggles), "weighting_model": "risk_parity"}),
    )
    with pytest.raises(ValueError, match="S5/S6.*risk"):
        oos_validation.validate_component_chain_variants(tuple(bad_risk_boundary))

    bad_universe = list(make_valid_chain())
    object.__setattr__(bad_universe[3], "universe_id", "different_universe")
    with pytest.raises(ValueError, match="universe_id"):
        oos_validation.validate_component_chain_variants(tuple(bad_universe))

    bad_execution_contract = list(make_valid_chain())
    object.__setattr__(bad_execution_contract[4], "execution_contract_version", "exec-v2")
    with pytest.raises(ValueError, match="execution_contract_version"):
        oos_validation.validate_component_chain_variants(tuple(bad_execution_contract))

    bad_data_snapshot = list(make_valid_chain())
    object.__setattr__(bad_data_snapshot[4], "data_identity_hash", "data-v2")
    with pytest.raises(ValueError, match="data snapshot"):
        oos_validation.validate_component_chain_variants(tuple(bad_data_snapshot))

    bad_accounting_policy = list(make_valid_chain())
    object.__setattr__(bad_accounting_policy[4], "accounting_policy_hash", "accounting-policy-v2")
    with pytest.raises(ValueError, match="accounting"):
        oos_validation.validate_component_chain_variants(tuple(bad_accounting_policy))

    bad_evaluation_calendar = list(make_valid_chain())
    object.__setattr__(bad_evaluation_calendar[4], "evaluation_calendar_hash", "evaluation-calendar-v2")
    with pytest.raises(ValueError, match="evaluation calendar"):
        oos_validation.validate_component_chain_variants(tuple(bad_evaluation_calendar))

    bad_benchmark_policy = list(make_valid_chain())
    object.__setattr__(bad_benchmark_policy[4], "benchmark_policy_hash", "benchmark-policy-v2")
    with pytest.raises(ValueError, match="benchmark"):
        oos_validation.validate_component_chain_variants(tuple(bad_benchmark_policy))

    bad_s0_clustering = list(make_valid_chain())
    object.__setattr__(bad_s0_clustering[0], "uses_clustering", True)
    with pytest.raises(ValueError, match="S0.*clustering"):
        oos_validation.validate_component_chain_variants(tuple(bad_s0_clustering))

    bad_s1_missing_clustering_toggle = list(make_valid_chain())
    object.__setattr__(
        bad_s1_missing_clustering_toggle[1],
        "component_toggles",
        oos_validation.freeze_for_identity({"momentum": "direct"}),
    )
    with pytest.raises(ValueError, match="S1.*clustering"):
        oos_validation.validate_component_chain_variants(tuple(bad_s1_missing_clustering_toggle))

    bad_s1_clustering = list(make_valid_chain())
    object.__setattr__(bad_s1_clustering[1], "uses_clustering", True)
    with pytest.raises(ValueError, match="S1.*clustering"):
        oos_validation.validate_component_chain_variants(tuple(bad_s1_clustering))

    bad_s2_clustering = list(make_valid_chain())
    object.__setattr__(bad_s2_clustering[2], "uses_clustering", False)
    with pytest.raises(ValueError, match="S2.*clustering"):
        oos_validation.validate_component_chain_variants(tuple(bad_s2_clustering))


def test_walk_forward_chain_rejects_accounting_contract_drift_and_non_continuous_folds() -> None:
    weeks = _weeks(320)
    policy = RollingWalkForwardPolicy(train_weeks=156, validation_weeks=52, test_weeks=20, step_weeks=20)
    folds = policy.generate_folds(weeks)
    initial_state = WalkForwardAccountState(
        cash=1000.0,
        positions={"510300.SH": 10.0},
        cost_basis={"510300.SH": 4.5},
        residual_orders=("buy-512880",),
        corporate_action_state={"510300.SH": "none"},
        last_valuation_date="w207",
        last_nav=1.0,
    )

    def contract_drift(fold, state: WalkForwardAccountState) -> WalkForwardAccountState:
        return state.replace(
            accounting_contract_version="accounting-v2",
            last_valuation_date=fold.test_weeks[-1],
            last_nav=state.last_nav + 0.01,
        )

    with pytest.raises(ValueError, match="accounting_contract_version"):
        run_walk_forward_state_chain(folds[:1], initial_state, contract_drift)

    gap_folds = (folds[0], folds[2])

    def transition(fold, state: WalkForwardAccountState) -> WalkForwardAccountState:
        return state.replace(last_valuation_date=fold.test_weeks[-1], last_nav=state.last_nav + 0.01)

    with pytest.raises(ValueError, match="continuous"):
        run_walk_forward_state_chain(gap_folds, initial_state, transition)

    runs = run_walk_forward_state_chain(folds[:2], initial_state, transition)

    assert runs[1].start_state is runs[0].end_state
    assert runs[1].start_state.cash == runs[0].end_state.cash
    assert runs[1].start_state.positions == runs[0].end_state.positions
    assert runs[1].start_state.residual_orders == runs[0].end_state.residual_orders
    assert runs[1].start_state.corporate_action_state == runs[0].end_state.corporate_action_state
