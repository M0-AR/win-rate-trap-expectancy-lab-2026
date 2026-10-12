"""Monte Carlo survival lab: fixed-fractional risk of ruin.

Draws with replacement from an empirical net-R distribution (block bootstrap by
default is simple iid here; dependence is handled in patterns.py streak test),
compounds equity with fixed-fractional sizing, and reports ruin probability,
median terminal equity, and P50/P5 drawdown paths.

Mirrors backtestbase 2026-01-25 method: 10k-run Monte Carlo, ruin = equity <
50% of start (half-ruin) or < 20% (full-ruin), reported separately.
"""
from __future__ import annotations
import numpy as np


def simulate(R: np.ndarray, risk_pct: float, n_trades: int = 200,
             n_paths: int = 5000, seed: int = 42) -> dict:
    rng = np.random.default_rng(seed)
    R = np.asarray(R, dtype=float)
    R = R[~np.isnan(R)]
    if len(R) == 0:
        return {"ruin_half": float("nan"), "ruin_full": float("nan")}
    paths = np.empty((n_paths, n_trades))
    terms = np.empty(n_paths)
    min_rel = np.empty(n_paths)
    for i in range(n_paths):
        draws = rng.choice(R, size=n_trades, replace=True)
        eq = 1.0
        mn = 1.0
        for r in draws:
            eq *= (1 + risk_pct * r)
            eq = max(eq, 1e-9)
            mn = min(mn, eq)
        terms[i] = eq
        min_rel[i] = mn
    return {
        "risk_pct": risk_pct,
        "n_trades": n_trades,
        "n_paths": n_paths,
        "ruin_half": float(np.mean(min_rel < 0.5)),
        "ruin_full": float(np.mean(terms < 0.2)),
        "median_terminal": float(np.median(terms)),
        "p5_terminal": float(np.percentile(terms, 5)),
        "p95_terminal": float(np.percentile(terms, 95)),
        "prob_profit": float(np.mean(terms > 1.0)),
    }
