"""correlation_representative strategy package (design §4/§8, Phase 3).

Complete fund-rotation strategy: after correlation clustering, each selected
cluster holds exactly ONE most-liquid representative ETF near the cluster
medoid. Clustering, quality gates and representative selection stay internal
to this package — the public layer only sees TargetWeightDecisions.
"""
