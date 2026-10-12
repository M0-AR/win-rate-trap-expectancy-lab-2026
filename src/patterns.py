"""Hidden-pattern discovery: what win rate hides.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def streak_stats(R: np.ndarray) -> dict:
    R = np.asarray(R, dtype=float)
    R = R[~np.isnan(R)]
    if len(R) == 0:
        return {}
    losses = (R <= 0).astype(int)
    best, cur = 0, 0
    for x in losses:
        cur = cur + 1 if x else 0
        best = max(best, cur)
    q = float(np.mean(losses))
    n = len(R)
    exp_run = float(np.log(n * (1 - q) + 1e-9) / np.log(1 / max(q, 1e-9))) if 0 < q < 1 else 0.0
    return {"n": n, "loss_rate": q, "longest_loss_streak": int(best),
            "expected_longest": round(exp_run, 2)}


def mae_mfe(df: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, t in trades.iterrows():
        try:
            window = df.loc[t["entry_date"]:t["exit_date"]]
        except Exception:
            continue
        if len(window) == 0:
            continue
        d = int(t["direction"])
        entry, risk = float(t["entry"]), float(t["risk_abs"])
        if risk <= 0:
            continue
        if d == 1:
            mfe = float((window["high"] - entry).max() / risk)
            mae = float((window["low"] - entry).min() / risk)
        else:
            mfe = float((entry - window["low"]).max() / risk)
            mae = float((entry - window["high"]).min() / risk)
        rows.append({"entry_date": t["entry_date"], "mfe_R": mfe, "mae_R": mae,
                     "realized_R": float(t["gross_R"]),
                     "left_on_table": float(max(mfe - float(t["gross_R"]), 0.0))})
    return pd.DataFrame(rows)
