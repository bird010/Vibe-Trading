from __future__ import annotations

import numpy as np
import pandas as pd


def make_features_df(n: int = 80, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for index in range(n):
        industry_index = index % 20
        rows.append(
            {
                "ts_code": f"{600000 + index:06d}.SH",
                "trade_date": "20260105",
                "industry": f"IND_{industry_index:02d}",
                "industry_rank": industry_index + 1,
                "total_industries": 20,
                "rank_in_industry": index % 10 + 1,
                "industry_stock_count": 10,
                "diffusion_score": float(rng.uniform(0.1, 0.85)),
                "leader_momentum": float(rng.normal(0.02, 0.03)),
                "net_big_inflow_5d": float(rng.normal(0.0, 1e7)),
                "industry_inflow_diffusion": float(rng.uniform(0.2, 0.8)),
                "crowding_score": float(rng.uniform(0.1, 0.9)),
                "reversal_5d": float(rng.normal(0.0, 0.05)),
                "neighbor_momentum": float(rng.normal(0.0, 0.05)),
                "industry_momentum_5d": float(rng.normal(0.01, 0.02)),
                "industry_momentum_20d": float(rng.normal(0.02, 0.04)),
                "volume_price_trend": float(rng.normal(0.0, 0.1)),
                "industry_corr_momentum": float(rng.normal(0.0, 0.05)),
                "fundamental_roe": float(rng.uniform(-5, 25)),
                "eps_growth": float(rng.uniform(-0.3, 0.5)),
                "gross_margin": float(rng.uniform(5, 60)),
                "rel_strength_20d": float(rng.normal(0.0, 0.1)),
                "low_volatility_signal": float(rng.uniform(0, 1)),
                "liquidity_signal": float(rng.uniform(0, 1)),
                "is_index_component": float(index % 2),
            }
        )
    return pd.DataFrame(rows)
