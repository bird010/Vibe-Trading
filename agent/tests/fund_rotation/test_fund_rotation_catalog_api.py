"""Phase 4 Task 1 — strategy catalog API tests (design §16/§18).

GET /stockpred/fund-rotation/strategies and /strategies/{strategy_id}:
responses come straight from the Catalog (no route-layer field duplication),
unknown strategies return the structured NOT_FOUND code, and a broken catalog
fails before any background task can be created.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backtest.fund_rotation.catalog import FundRotationStrategyCatalog
from backtest.fund_rotation.contracts import FundRotationStrategyDescriptor
from backtest.fund_rotation.strategies.correlation_all_members.strategy import (
    CorrelationAllMembersStrategy,
)
from backtest.fund_rotation.strategies.registry import (
    default_fund_rotation_strategies,
)
from src.api.fund_rotation_routes import register_fund_rotation_routes


def _app(tmp_path) -> FastAPI:
    app = FastAPI()
    register_fund_rotation_routes(app, tmp_path, lambda: None, lambda: None)
    return app


def _client(tmp_path) -> TestClient:
    return TestClient(_app(tmp_path))


class TestStrategyList:
    def test_list_sorted_with_catalog_version(self, tmp_path):
        response = _client(tmp_path).get("/stockpred/fund-rotation/strategies")
        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "RESEARCH_ONLY"
        assert body["catalog_version"]
        ids = [s["strategy_id"] for s in body["strategies"]]
        assert ids == [
            "ai_rotation_r05_mom_persist",
            "ai_rotation_r06_rank_buffer",
            "ai_rotation_r07_tail_persist",
            "ai_rotation_r11_persist_geom",
            "ai_rotation_r12_nondecay_geom",
            "ai_rotation_r13_arith_persist",
            "ai_rotation_r14_median_persist",
            "ai_rotation_r15_weighted_persist",
            "ai_rotation_r16_rank_consensus",
            "ai_rotation_r17_winsor_geom",
            "ai_rotation_r18_min_persist",
            "ai_rotation_r19_top2_cash",
            "ai_rotation_r20_rank_frontload",
            "ai_rotation_r21_harmonic_persist",
            "ai_rotation_r22_path_consistency",
            "ai_rotation_r23_downside_geom",
            "ai_rotation_r24_dispersion_geom",
            "ai_rotation_r25_rep_persist_geom",
            "ai_rotation_r26_path_vol_geom",
            "ai_rotation_r27_breadth_persist_geom",
            "ai_rotation_r28_size_reliability_geom",
            "ai_rotation_r29_invvol_slots",
            "ai_rotation_r30_endpoint_breadth_geom",
            "ai_rotation_r31_fast_exit",
            "ai_rotation_r32_market_regime",
            "ai_rotation_r33_quality_fallback",
            "ai_rotation_r34_staged_reentry",
            "ai_rotation_r35_short_gap_reentry",
            "ai_rotation_r36_tail_slot_full_entry",
            "ai_rotation_r37_decelerating_full_entry",
            "ai_rotation_r38_replacement_full_entry",
            "ai_rotation_r39_incumbent_carry",
            "ai_rotation_r40_single_name_ceiling",
            "ai_rotation_r41_breadth_gated_carry",
            "ai_rotation_r42_single_incumbent_half_carry",
            "ai_rotation_r43_multi_new_breadth_gate",
            "ai_rotation_r44_persistent_incumbent_carry",
            "ai_rotation_r45_cash_floor_carry",
            "ai_rotation_r46_cash_floor_tight_carry",
            "ai_rotation_r47_breadth_tight_floor",
            "ai_rotation_r48_cash_floor_very_tight",
            "ai_rotation_r49_cash_floor_micro",
            "ai_rotation_r50_cash_floor_nano",
            "ai_rotation_r51_cash_floor_pico",
            "ai_rotation_r52_cash_floor_femto",
            "ai_rotation_r53_cash_floor_atto",
            "ai_rotation_r54_cash_floor_zepto",
            "ai_rotation_r55_cash_floor_yotta",
            "ai_rotation_r56_cash_floor_ronna",
            "ai_rotation_r57_three_factor_representative",
            "ai_rotation_r58_r39_signal_r57",
            "ai_rotation_r59_r39_signal_r57_positive_slope",
            "ai_rotation_r60_r59_medium_trend_gate",
            "ai_rotation_r61_r59_dual_horizon_score",
            "ai_rotation_r62_r59_true_invvol",
            "ai_rotation_r63_r59_rank_buffer",
            "ai_rotation_r64_direct_corr_diversification",
            "ai_rotation_r65_r64_direct_corr_rank_buffer",
            "ai_rotation_r66_lazy_correlation",
            "ai_rotation_r67_r39_rank_buffer",
            "ai_rotation_r69_r39_transition_cap_50",
            "ai_rotation_r70_r39_transition_cap_25",
            "ai_rotation_r71_r39_capacity_aware_representative",
            "ai_rotation_r72_r39_absolute_momentum",
            "ai_rotation_r73_r39_multi_horizon_rank",
            "ai_rotation_r74_r39_vol_adjusted_score",
            "ai_rotation_r75_r39_vol_target",
            "ai_rotation_r76_cash_defense_baseline",
            "ai_rotation_r76_fixed_short_bond",
            "ai_rotation_r77_defense_relative_momentum",
            "ai_rotation_r78_survivor_combo",
            "ai_rotation_r79_economic_role_members",
            "ai_rotation_r80_economic_role_fixed_rep",
            "ai_rotation_r81_economic_role_dynamic_rep",
            "correlation_all_members", "correlation_representative",
        ]

    def test_entries_carry_catalog_fields(self, tmp_path):
        body = _client(tmp_path).get("/stockpred/fund-rotation/strategies").json()
        catalog = FundRotationStrategyCatalog(list(default_fund_rotation_strategies()))
        entries = {e.strategy_id: e for e in catalog.list()}
        for item in body["strategies"]:
            registered = entries[item["strategy_id"]]
            # Straight from the catalog descriptor / snapshot.
            assert item["name"] == registered.name
            assert item["description"] == registered.description
            assert item["interface_version"] == registered.interface_version
            assert item["implementation_hash"] == registered.implementation_hash
            assert list(item["supported_universe"]) == list(
                registered.supported_universe
            )
            # Requirements resolved from the default config.
            binding = catalog.resolve(item["strategy_id"], {})
            req = binding.spec.resolved_requirements
            assert item["warmup_trade_days"] == req.warmup_trade_days
            assert list(item["required_datasets"]) == list(req.required_datasets)
            assert list(item["required_fields"]) == list(req.required_fields)
            assert item["frequency"] == req.frequency

    def test_catalog_version_is_deterministic(self, tmp_path):
        first = _client(tmp_path).get("/stockpred/fund-rotation/strategies").json()
        second = _client(tmp_path).get("/stockpred/fund-rotation/strategies").json()
        assert first["catalog_version"] == second["catalog_version"]


class TestStrategyDetail:
    def test_detail_includes_schema_defaults_roles_and_warning(self, tmp_path):
        response = _client(tmp_path).get(
            "/stockpred/fund-rotation/strategies/correlation_representative"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["strategy_id"] == "correlation_representative"
        # JSON Schema for the dynamic frontend form, with version + content hash.
        schema = body["config_schema"]
        assert schema["title"] == "CorrelationRepresentativeConfig"
        assert body["config_schema_version"]
        assert body["config_schema_hash"]
        # Cacheability: RFC 7232 quoted ETag carrying the schema content hash.
        assert response.headers.get("ETag") == '"' + body["config_schema_hash"] + '"'
        # Resolved defaults and per-parameter descriptions.
        assert body["default_config"]["representative_min_cluster_corr"] == 0.85
        assert "聚类簇数量" in body["parameter_descriptions"]["k"]
        # Research-mode warning and strategy-specific artifact roles.
        assert body["mode"] == "RESEARCH_ONLY"
        assert "RESEARCH_ONLY" in body["research_mode_warning"]
        assert {
            "cluster_history", "gates", "representatives", "exclusions", "decisions",
        } <= set(body["artifact_roles"])

    def test_if_none_match_returns_304(self, tmp_path):
        client = _client(tmp_path)
        url = "/stockpred/fund-rotation/strategies/correlation_representative"
        etag = client.get(url).headers["ETag"]
        cached = client.get(url, headers={"If-None-Match": etag})
        assert cached.status_code == 304
        assert cached.headers["ETag"] == etag

    def test_baseline_detail_roles(self, tmp_path):
        body = _client(tmp_path).get(
            "/stockpred/fund-rotation/strategies/correlation_all_members"
        ).json()
        assert "cluster_history" in body["artifact_roles"]
        assert body["default_config"]["k"] == 8

    @pytest.mark.parametrize(
        "strategy_id",
        [
            "ai_rotation_r79_economic_role_members",
            "ai_rotation_r80_economic_role_fixed_rep",
            "ai_rotation_r81_economic_role_dynamic_rep",
        ],
    )
    def test_economic_role_manifest_is_read_only_in_catalog_schema(
        self, tmp_path, strategy_id
    ):
        body = _client(tmp_path).get(
            f"/stockpred/fund-rotation/strategies/{strategy_id}"
        ).json()

        manifest = body["config_schema"]["properties"]["fixed_role_manifest"]
        assert manifest["readOnly"] is True
        assert body["default_config"]["fixed_role_manifest"]

    def test_unknown_strategy_returns_structured_not_found(self, tmp_path):
        response = _client(tmp_path).get(
            "/stockpred/fund-rotation/strategies/does_not_exist"
        )
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert detail["code"] == "FUND_ROTATION_STRATEGY_NOT_FOUND"
        assert "does_not_exist" in detail["message"]


class TestBrokenCatalogFailsBeforeServing:
    def test_incompatible_interface_fails_registration(self, tmp_path):
        """A corrupted catalog entry must fail before any background task can
        be created — never a half-built list (design §16.3)."""
        import src.api.fund_rotation_routes as routes_mod

        bad_descriptor = FundRotationStrategyDescriptor(
            id="bad", name="bad", description="d",
            interface_version="9.9", supported_universe=("etf",), deterministic=True,
        )

        class _Bad:
            descriptor = bad_descriptor
            config_model = CorrelationAllMembersStrategy.config_model

            def resolve_requirements(self, config):
                raise NotImplementedError

            def create_session(self, initialization, config):
                raise NotImplementedError

        def broken_whitelist():
            return (_Bad,)

        original = routes_mod.default_fund_rotation_strategies
        routes_mod.default_fund_rotation_strategies = broken_whitelist
        try:
            with pytest.raises(Exception) as exc_info:
                app = FastAPI()
                register_fund_rotation_routes(
                    app, tmp_path, lambda: None, lambda: None,
                )
            assert "FUND_ROTATION_INTERFACE_INCOMPATIBLE" in str(
                getattr(exc_info.value, "code", exc_info.value)
            ) or "interface" in str(exc_info.value).lower()
        finally:
            routes_mod.default_fund_rotation_strategies = original
