from __future__ import annotations

from src.factors.base import Alpha
from src.stockpred.strategies.catalog import StrategyCatalog


class _Registry:
    def list(self, *, universe: str | None = None, **_: object) -> list[str]:
        assert universe == "equity_cn"
        return ["alpha101_1"]

    def get(self, alpha_id: str) -> Alpha:
        assert alpha_id == "alpha101_1"
        return Alpha(
            id=alpha_id,
            zoo="alpha101",
            module_path="src.factors.zoo.alpha101.alpha_1",
            meta={
                "nickname": "Alpha #1",
                "columns_required": ["close", "volume"],
                "min_warmup_bars": 20,
                "formula_latex": "rank(close)",
                "theme": ["momentum"],
            },
        )


def test_catalog_exposes_graph_and_registered_alpha() -> None:
    catalog = StrategyCatalog(registry=_Registry())

    assert [item.id for item in catalog.list()] == ["alpha101_1", "stockpred_graph"]
    alpha = catalog.require("alpha101_1")
    assert alpha.kind == "alpha_zoo"
    assert alpha.zoo == "alpha101"
    assert alpha.columns_required == ("close", "volume")


def test_catalog_rejects_unknown_strategy() -> None:
    catalog = StrategyCatalog(registry=_Registry())

    try:
        catalog.require("missing")
    except KeyError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("expected unknown strategy to raise KeyError")
