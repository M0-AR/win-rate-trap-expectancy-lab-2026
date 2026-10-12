"""Core trading statistics in R-multiples.

All formulas verified against 2026 literature:
- Expectancy E = p*W - q*L  (Van Tharp R-multiples; traderssecondbrain 2026-09-11,
  curvedtrading 2026-04-02, tradejournal.ai 2026-08-31, completetradersedge 2026-05-07)
- Breakeven p* = 1/(1+B),  B* = (1-p)/p
- Profit factor = p*B/(1-p)  (gross win / gross loss)
- Kelly f* = p - q/B  (fraction of bankroll; professionals use 0.25-0.5x Kelly)
- Wilson 95% CI for win rate; bootstrap CI for expectancy.

Conventions
-----------
- 1R = planned initial risk (entry - stop) in price terms.
- Net R per trade = realized PnL / planned risk - cost_R (costs always subtracted).
- Losses stored as negative R, wins as positive R.
"""
from __future__ import annotations
import numpy as np
from scipy import stats


def to_R(pnl: np.ndarray, risk: np.ndarray, cost_R: float = 0.0) -> np.ndarray:
    pnl = np.asarray(pnl, dtype=float)
    risk = np.asarray(risk, dtype=float)
    risk = np.where(risk <= 0, np.nan, risk)
    return pnl / risk - cost_R


def expectancy_R(R: np.ndarray) -> float:
    R = np.asarray(R, dtype=float)
    R = R[~np.isnan(R)]
    if len(R) == 0:
        return float("nan")
    return float(np.mean(R))


def win_rate(R: np.ndarray) -> float:
    R = np.asarray(R, dtype=float)
    R = R[~np.isnan(R)]
    if len(R) == 0:
        return float("nan")
    return float(np.mean(R > 0))


def avg_win_R(R: np.ndarray) -> float:
    R = np.asarray(R, dtype=float)
    w = R[R > 0]
    return float(np.mean(w)) if len(w) else 0.0


def avg_loss_R(R: np.ndarray) -> float:
    R = np.asarray(R, dtype=float)
    l = R[R <= 0]
    return float(np.mean(np.abs(l))) if len(l) else 0.0


def expectancy_decomposed(R: np.ndarray) -> dict:
    p = win_rate(R)
    W = avg_win_R(R)
    L = avg_loss_R(R)
    E = expectancy_R(R)
    B = (W / L) if L > 0 else float("inf")
    return {"n": int(np.sum(~np.isnan(R))), "p": p, "W": W, "L": L, "B": B, "E": E,
            "E_check": p * W - (1 - p) * L}


def breakeven_win_rate(B: float) -> float:
    return 1.0 / (1.0 + B)


def breakeven_B(p: float) -> float:
    return (1 - p) / p if p > 0 else float("inf")


def profit_factor(R: np.ndarray) -> float:
    R = np.asarray(R, dtype=float)
    gross_win = np.sum(R[R > 0])
    gross_loss = np.sum(np.abs(R[R <= 0]))
    if gross_loss == 0:
        return float("inf") if gross_win > 0 else float("nan")
    return float(gross_win / gross_loss)


def kelly_fraction(p: float, B: float) -> float:
    """Kelly f* = p - (1-p)/B. Negative => no edge, bet 0."""
    if B <= 0:
        return 0.0
    return float(p - (1 - p) / B)


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (float("nan"), float("nan"))
    phat = k / n
    denom = 1 + z * z / n
    centre = phat + z * z / (2 * n)
    delta = z * np.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (float((centre - delta) / denom), float((centre + delta) / denom))


def expectancy_ttest(R: np.ndarray) -> dict:
    R = np.asarray(R, dtype=float)
    R = R[~np.isnan(R)]
    if len(R) < 3:
        return {"t": float("nan"), "p_value": float("nan")}
    t, pv = stats.ttest_1samp(R, 0.0)
    return {"t": float(t), "p_value": float(pv)}


def bootstrap_ci(R: np.ndarray, n_boot: int = 2000, seed: int = 42) -> tuple:
    rng = np.random.default_rng(seed)
    R = np.asarray(R, dtype=float)
    R = R[~np.isnan(R)]
    if len(R) == 0:
        return (float("nan"), float("nan"))
    means = [float(np.mean(rng.choice(R, size=len(R), replace=True))) for _ in range(n_boot)]
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def max_drawdown(equity: np.ndarray) -> dict:
    eq = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / np.where(peak == 0, 1, peak)
    return {"max_dd": float(np.min(dd)), "max_dd_pct": float(np.min(dd) * 100)}
