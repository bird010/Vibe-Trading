"""Deterministic, auditable Economic Role classifier and representatives."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

CN_DEFENSIVE_EQUITY = "CN_DEFENSIVE_EQUITY"
CN_GROWTH_EQUITY = "CN_GROWTH_EQUITY"
OVERSEAS_GROWTH_EQUITY = "OVERSEAS_GROWTH_EQUITY"
GOLD = "GOLD"
BOND = "BOND"
ROLE_IDS = (
    CN_DEFENSIVE_EQUITY,
    CN_GROWTH_EQUITY,
    OVERSEAS_GROWTH_EQUITY,
    GOLD,
    BOND,
)

MATCHED = "MATCHED"
UNCLASSIFIED = "UNCLASSIFIED"
AMBIGUOUS = "AMBIGUOUS"
EMPTY_NAME = "EMPTY_NAME"

ROLE_CLASSIFIER_VERSION = "1"
ROLE_RULE_VERSION = "1"

_EXCLUSIONS = {
    CN_DEFENSIVE_EQUITY: (
        "港股", "恒生", "纳指", "纳斯达克", "标普", "美国", "全球",
        "黄金", "债", "货币", "商品",
    ),
    CN_GROWTH_EQUITY: (
        "港股", "恒生", "纳指", "纳斯达克", "标普", "美国", "全球",
        "黄金", "债", "货币", "商品",
    ),
    OVERSEAS_GROWTH_EQUITY: ("行业", "主题", "医药", "芯片", "军工"),
    GOLD: ("黄金股票", "黄金股", "黄金产业", "黄金矿业", "金矿", "贵金属股票"),
    BOND: (
        "可转债", "信用债", "公司债", "城投债", "地方政府债", "同业存单",
        "中短债", "短融", "货币",
    ),
}

_RULES = {
    CN_DEFENSIVE_EQUITY: (
        (1, "红利低波动", "红利低波动"),
        (1, "红利低波", "红利低波"),
        (2, "红利", "红利"),
        (3, "价值", "价值"),
    ),
    CN_GROWTH_EQUITY: (
        (1, "创业板50", "创业板50"),
        (2, "科创50", "科创50"),
        (3, "创业板", "创业板"),
    ),
    OVERSEAS_GROWTH_EQUITY: (
        (1, "纳斯达克100", "纳斯达克100"),
        (1, "纳指100", "纳指100"),
        (1, "NASDAQ100", "NASDAQ100"),
        (2, "标普500", "标普500"),
        (2, "S&P500", "S&P500"),
    ),
    GOLD: (
        (1, "黄金ETF", "黄金ETF"),
        (1, "上海金ETF", "上海金ETF"),
    ),
    BOND: (
        (1, "10年国债", "10年国债"),
        (1, "10年期国债", "10年期国债"),
        (1, "30年国债", "30年国债"),
        (1, "30年期国债", "30年期国债"),
        (1, "政策性金融债", "政策性金融债"),
        (1, "政策金融债", "政策金融债"),
        (1, "政金债", "政金债"),
    ),
}


@dataclass(frozen=True)
class RoleClassification:
    status: str
    role_id: str | None = None
    tier: int | None = None
    matched_rule: str | None = None
    exclusion_reason: str | None = None


def normalize_name(name: object) -> str:
    """Normalize spaces and punctuation without broadening semantic matches."""
    if name is None:
        return ""
    text = str(name).strip().upper()
    return re.sub(r"[\s\-_/()（）·.&]+", "", text)


def _normal_rule(rule: str) -> str:
    return normalize_name(rule)


def _matches_role(name: str, role_id: str) -> tuple[int, str] | None:
    normalized = normalize_name(name)
    if not normalized:
        return None
    if any(normalize_name(exclusion) in normalized for exclusion in _EXCLUSIONS[role_id]):
        return None
    matches = [
        (tier, rule_name)
        for tier, rule_name, phrase in _RULES[role_id]
        if _normal_rule(phrase) in normalized
    ]
    if not matches:
        return None
    return min(matches, key=lambda item: (item[0], len(item[1]) * -1, item[1]))


def classify_fund_name(name: object) -> RoleClassification:
    """Classify one name; conflicts fail closed instead of choosing a priority."""
    if not normalize_name(name):
        return RoleClassification(status=EMPTY_NAME, exclusion_reason="EMPTY_NAME")
    matches = {
        role_id: _matches_role(str(name), role_id)
        for role_id in ROLE_IDS
    }
    matches = {role_id: value for role_id, value in matches.items() if value is not None}
    if len(matches) != 1:
        if len(matches) > 1:
            return RoleClassification(
                status=AMBIGUOUS,
                exclusion_reason="AMBIGUOUS_ROLE_MATCH",
            )
        return RoleClassification(status=UNCLASSIFIED, exclusion_reason="NO_ROLE_MATCH")
    role_id, (tier, rule_name) = next(iter(matches.items()))
    return RoleClassification(
        status=MATCHED,
        role_id=role_id,
        tier=tier,
        matched_rule=rule_name,
    )


def classify_universe(records: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """Return one serializable classification row per effective-universe code."""
    result: list[dict[str, object]] = []
    for record in records:
        code = str(record.get("ts_code", ""))
        classification = classify_fund_name(record.get("name"))
        result.append({
            "ts_code": code,
            "name": str(record.get("name", "")),
            **asdict(classification),
        })
    return sorted(result, key=lambda row: str(row["ts_code"]))


def role_rule_hash() -> str:
    """Hash classifier versions and canonical rule content."""
    payload = {
        "classifier_version": ROLE_CLASSIFIER_VERSION,
        "rule_version": ROLE_RULE_VERSION,
        "roles": {
            role_id: {
                "rules": [list(rule) for rule in _RULES[role_id]],
                "exclusions": list(_EXCLUSIONS[role_id]),
            }
            for role_id in ROLE_IDS
        },
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def select_fixed_representative(
    manifest: Sequence[str],
    *,
    candidates: set[str],
    eligible: set[str],
    adv: Mapping[str, float],
) -> str | None:
    """Select the first manifest code that survives all current gates."""
    del adv  # Fixed selection intentionally does not use ADV as a tie-break.
    allowed = set(candidates) & set(eligible)
    return next((str(code) for code in manifest if str(code) in allowed), None)


def select_dynamic_representative(
    candidates: Mapping[str, tuple[int, float]],
    *,
    eligible: set[str],
) -> str | None:
    """Select by semantic tier, then ADV descending, then code ascending."""
    valid = [
        (str(code), int(tier), float(adv))
        for code, (tier, adv) in candidates.items()
        if str(code) in eligible and math_is_finite(float(adv))
    ]
    if not valid:
        return None
    highest_tier = min(item[1] for item in valid)
    tiered = [item for item in valid if item[1] == highest_tier]
    return min(tiered, key=lambda item: (-item[2], item[0]))[0]


def math_is_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))
