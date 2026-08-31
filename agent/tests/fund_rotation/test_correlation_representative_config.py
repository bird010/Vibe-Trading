"""Phase 3 Task 1 — correlation_representative strategy config tests.

Frozen Pydantic config with the design §4 field set: gate thresholds belong to
this strategy (never to the common Runner/ExecutionConfig), warn/reject
threshold pairs are validated against conflicting combinations, and the model
exposes a stable JSON Schema for the frontend dynamic form.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backtest.fund_rotation.strategies.correlation_representative.config import (
    CorrelationRepresentativeConfig,
)


# Design §4 defaults (exact field names, no legacy aliases).
DESIGN_DEFAULTS = {
    "k": 8,
    "top_n": 3,
    "correlation_lookback_weeks": 52,
    "momentum_window_weeks": 4,
    "recluster_interval_weeks": 26,
    "min_valid_weeks": 20,
    "min_pairwise_weeks": 20,
    "representative_candidate_count": 5,
    "representative_min_cluster_corr": 0.85,
    "representative_liquidity_window_days": 20,
    "representative_min_liquidity_observations": 15,
    "max_cluster_share_warn": 0.50,
    "max_cluster_share_reject": 0.80,
    "min_effective_cluster_count_warn": 4.0,
    "min_effective_cluster_count_reject": 2.5,
    "representative_relaxed_selection": False,
    "cluster_cross_sectional_demean": False,
}


class TestDefaults:
    def test_defaults_match_design_section_4_exactly(self):
        cfg = CorrelationRepresentativeConfig()
        assert set(CorrelationRepresentativeConfig.model_fields) == set(DESIGN_DEFAULTS)
        for field_name, expected in DESIGN_DEFAULTS.items():
            assert getattr(cfg, field_name) == expected, field_name

    def test_no_legacy_alias_fields(self):
        fields = set(CorrelationRepresentativeConfig.model_fields)
        assert "momentum_threshold" not in fields
        assert "initial_capital" not in fields
        assert "start_date" not in fields


class TestSchemaAndValidation:
    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(not_a_field=1)

    def test_config_is_immutable(self):
        cfg = CorrelationRepresentativeConfig()
        with pytest.raises(ValidationError):
            cfg.k = 5  # frozen

    def test_range_violations_rejected(self):
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(k=0)
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(top_n=0)
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(representative_candidate_count=0)
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(representative_liquidity_window_days=0)
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(
                representative_min_liquidity_observations=0
            )
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(correlation_lookback_weeks=0)
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(momentum_window_weeks=0)
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(recluster_interval_weeks=0)

    def test_top_n_must_not_exceed_k(self):
        with pytest.raises(ValidationError, match="top_n"):
            CorrelationRepresentativeConfig(k=3, top_n=5)

    def test_min_pairwise_must_not_exceed_lookback(self):
        with pytest.raises(ValidationError, match="min_pairwise_weeks"):
            CorrelationRepresentativeConfig(
                correlation_lookback_weeks=20, min_pairwise_weeks=30,
            )

    def test_min_valid_weeks_must_not_exceed_lookback(self):
        with pytest.raises(ValidationError, match="min_valid_weeks"):
            CorrelationRepresentativeConfig(
                correlation_lookback_weeks=20, min_valid_weeks=30,
            )
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(min_valid_weeks=0)

    def test_momentum_window_must_be_subset_of_lookback(self):
        with pytest.raises(ValidationError, match="momentum_window_weeks"):
            CorrelationRepresentativeConfig(
                correlation_lookback_weeks=20, momentum_window_weeks=20,
            )

    def test_liquidity_observations_must_fit_window(self):
        with pytest.raises(ValidationError, match="liquidity"):
            CorrelationRepresentativeConfig(
                representative_liquidity_window_days=10,
                representative_min_liquidity_observations=15,
            )

    def test_cluster_corr_threshold_bounds(self):
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(representative_min_cluster_corr=0.0)
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(representative_min_cluster_corr=1.5)

    def test_experiment_flags_default_to_legacy_behavior(self):
        cfg = CorrelationRepresentativeConfig()
        assert cfg.representative_relaxed_selection is False
        assert cfg.cluster_cross_sectional_demean is False


class TestGateThresholdConflicts:
    """Conflicting gate combinations must be rejected before launch (§4)."""

    def test_share_warn_must_be_strictly_below_reject(self):
        with pytest.raises(ValidationError, match="max_cluster_share"):
            CorrelationRepresentativeConfig(
                max_cluster_share_warn=0.80, max_cluster_share_reject=0.80,
            )
        with pytest.raises(ValidationError, match="max_cluster_share"):
            CorrelationRepresentativeConfig(
                max_cluster_share_warn=0.90, max_cluster_share_reject=0.80,
            )

    def test_share_thresholds_must_be_valid_fractions(self):
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(max_cluster_share_warn=0.0)
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(max_cluster_share_reject=1.5)

    def test_effective_count_warn_must_be_strictly_above_reject(self):
        with pytest.raises(ValidationError, match="min_effective_cluster_count"):
            CorrelationRepresentativeConfig(
                min_effective_cluster_count_warn=2.5,
                min_effective_cluster_count_reject=2.5,
            )
        with pytest.raises(ValidationError, match="min_effective_cluster_count"):
            CorrelationRepresentativeConfig(
                min_effective_cluster_count_warn=2.0,
                min_effective_cluster_count_reject=2.5,
            )

    def test_effective_count_thresholds_must_be_positive(self):
        with pytest.raises(ValidationError):
            CorrelationRepresentativeConfig(min_effective_cluster_count_reject=0.0)


class TestJsonSchema:
    def test_schema_has_title_type_defaults_bounds_descriptions(self):
        schema = CorrelationRepresentativeConfig.model_json_schema()
        assert schema["title"] == "CorrelationRepresentativeConfig"
        assert schema["type"] == "object"
        props = schema["properties"]
        assert props["k"]["default"] == 8
        assert props["k"]["minimum"] == 1
        assert props["top_n"]["default"] == 3
        assert props["representative_min_cluster_corr"]["default"] == 0.85
        assert props["max_cluster_share_warn"]["default"] == 0.5
        assert "聚类簇数量" in props["k"]["description"]

    def test_json_round_trip(self):
        cfg = CorrelationRepresentativeConfig(k=4, top_n=2, correlation_lookback_weeks=30)
        dumped = cfg.model_dump(mode="json")
        restored = CorrelationRepresentativeConfig.model_validate(dumped)
        assert restored == cfg
