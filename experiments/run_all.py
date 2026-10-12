"""End-to-end verification: Trader1 vs Trader2 parable on LIVE public data.

E1 parable replication (synthetic R matching video + live-data twins)
E2 breakeven grid on live data (k = 1, 1.5, 2, 3 across symbols)
E3 survival: risk% sweep 1/2/3/5/8/10 with Monte Carlo ruin + live equity
E4 scale_out vs all_in (video's 1R->breakeven->kR)
E5 pause filter: skip trading when trailing 10-trade win rate < 50%
E6 hidden patterns: disposition, MAE/MFE left-on-table, streaks, cost drag,
    win-rate/expectancy decoupling scatter

Outputs: results/summary.json, results/trades_*.csv, results/grid.csv,
         results/montecarlo.csv, figures/*.png, results/data_manifest.csv
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

from src.data_loader import load_all
from src.metrics import (expectancy_decomposed, profit_factor, kelly_fraction,
                         breakeven_win_rate, bootstrap_ci, expectancy_ttest,
                         max_drawdown, wilson_ci)
from src.strategies import signals_sweep_reclaim, signals_breakout
from src.backtest import backtest
from src.montecarlo import simulate
from src.patterns import streak_stats, mae_mfe


def synth_R(p: float, W: float, L: float, n: int, seed: int):
    rng = np.random.default_rng(seed)
    wins = rng.choice([True, False], size=n, p=[p, 1 - p])
    # add noise around W/L so stats are not degenerate
    out = np.where(wins, rng.normal(W, 0.15 * W + 0.05, n),
                   -np.abs(rng.normal(L, 0.15 * L + 0.05, n)))
    return out


def fig_equity(curves: dict, path: Path, title: str):
    plt.figure(figsize=(10, 5))
    for k, s in curves.items():
        s.index = pd.to_datetime(s.index)
        plt.plot(s.index, s.values / s.values[0], label=k)
    plt.title(title)
    plt.ylabel("Growth of $1")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/experiment.yaml")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    Rdir, Fdir, Ddir = Path(cfg["RESULTS_DIR"]), Path(cfg["FIGURES_DIR"]), Path(cfg["DATA_DIR"])
    Rdir.mkdir(exist_ok=True); Fdir.mkdir(exist_ok=True); Ddir.mkdir(exist_ok=True)

    data = load_all(cfg["symbols"], cfg["start"], cfg["end"], str(Ddir), cfg["seed"])
    manifest = [{"symbol": s, "rows": len(df), "from": str(df.index[0].date()),
                 "to": str(df.index[-1].date()), "source": src} for s, (df, src) in data.items()]
    pd.DataFrame(manifest).to_csv(Rdir / "data_manifest.csv", index=False)

    out: dict = {"config": cfg, "manifest": manifest, "sources": [m["source"] for m in manifest]}

    # ---------------- E1: parable ----------------
    t1 = synth_R(0.80, 0.30, 3.0, 200, cfg["seed"]) - cfg["cost_R"]  # high win, tiny wins, huge losses
    t2 = synth_R(0.50, 1.80, 1.0, 200, cfg["seed"] + 1) - cfg["cost_R"]
    d1, d2 = expectancy_decomposed(t1), expectancy_decomposed(t2)
    # live twins: same backtest engine, different k to force profiles
    live_trades = {}
    for sym, (df, src) in data.items():
        sig = signals_sweep_reclaim(df, cfg["entry_lookback"], cfg["atr_window"])
        tr_high, _ = backtest(df, sig, k=0.6, risk_pct=0.03, cost_R=cfg["cost_R"], mode="all_in")
        tr_bal, _ = backtest(df, sig, k=2.0, risk_pct=0.03, cost_R=cfg["cost_R"], mode="scale_out")
        live_trades[sym] = (tr_high, tr_bal)
    # aggregate live R
    r_high = np.concatenate([t["net_R"].values for t, _ in live_trades.values() if len(t)]) \
        if any(len(t) for t, _ in live_trades.values()) else np.array([])
    r_bal = np.concatenate([t[1]["net_R"].values for t in live_trades.values() if len(t[1])]) \
        if any(len(t[1]) for t in live_trades.values()) else np.array([])
    out["E1_parable_synthetic"] = {
        "Trader1_80pct": {**d1, "PF": profit_factor(t1), "kelly": kelly_fraction(d1["p"], d1["B"])},
        "Trader2_50pct": {**d2, "PF": profit_factor(t2), "kelly": kelly_fraction(d2["p"], d2["B"])},
        "verdict": "Trader1 E<0 despite p=0.8; Trader2 E>0 at p~0.5. Win rate alone misranks them."}
    if len(r_high) and len(r_bal):
        out["E1_live_twins"] = {
            "high_win_small_target_k0.6": expectancy_decomposed(r_high),
            "balanced_scaleout_k2": expectancy_decomposed(r_bal)}
    print("[E1]", json.dumps(out["E1_parable_synthetic"], indent=1)[:600])

    # ---------------- E2: breakeven grid ----------------
    grid = []
    for sym, (df, src) in data.items():
        for k in cfg["targets_R"]:
            for mode in ["all_in", "scale_out"]:
                sig = signals_sweep_reclaim(df, cfg["entry_lookback"], cfg["atr_window"])
                tr, eq = backtest(df, sig, k=k, risk_pct=0.03, cost_R=cfg["cost_R"], mode=mode)
                if len(tr) < 20:
                    continue
                dd = expectancy_decomposed(tr["net_R"].values)
                lo, hi = bootstrap_ci(tr["net_R"].values, cfg["n_bootstrap"], cfg["seed"])
                grid.append({"symbol": sym, "k": k, "mode": mode, "n": dd["n"], "p": round(dd["p"], 4),
                             "W": round(dd["W"], 3), "L": round(dd["L"], 3), "B": round(dd["B"], 3),
                             "E": round(dd["E"], 4), "PF": round(profit_factor(tr["net_R"].values), 3),
                             "breakeven_p": round(breakeven_win_rate(dd["B"] if np.isfinite(dd["B"]) else 2.0), 4),
                             "E_ci_lo": round(lo, 4), "E_ci_hi": round(hi, 4),
                             "terminal": round(eq.iloc[-1] / eq.iloc[0], 4)})
    grid_df = pd.DataFrame(grid)
    grid_df.to_csv(Rdir / "grid.csv", index=False)
    out["E2_grid_rows"] = len(grid_df)
    out["E2_best5_by_E"] = grid_df.sort_values("E", ascending=False).head(5).to_dict("records") if len(grid_df) else []
    print(f"[E2] grid rows={len(grid_df)}")

    # ---------------- E2b: trend control (breakout) + direction split ----------------
    # Same engine/costs/sizing, only signal logic differs. Tests whether expectancy
    # sign follows signal+exit (B), not win rate alone. Also splits long vs short
    # to expose bull-market regime asymmetry (hidden pattern H4).
    grid_b, dir_rows = [], []
    for sym, (df, src) in data.items():
        for k in cfg["targets_R"]:
            for mode in ["all_in", "scale_out"]:
                sig = signals_breakout(df, cfg["entry_lookback"], cfg["atr_window"])
                tr, eq = backtest(df, sig, k=k, risk_pct=0.03, cost_R=cfg["cost_R"], mode=mode)
                if len(tr) < 20:
                    continue
                dd = expectancy_decomposed(tr["net_R"].values)
                lo, hi = bootstrap_ci(tr["net_R"].values, cfg["n_bootstrap"], cfg["seed"])
                grid_b.append({"symbol": sym, "k": k, "mode": mode, "n": dd["n"], "p": round(dd["p"], 4),
                               "W": round(dd["W"], 3), "L": round(dd["L"], 3), "B": round(dd["B"], 3),
                               "E": round(dd["E"], 4), "PF": round(profit_factor(tr["net_R"].values), 3),
                               "E_ci_lo": round(lo, 4), "E_ci_hi": round(hi, 4)})
        sig_sw = signals_sweep_reclaim(df, cfg["entry_lookback"], cfg["atr_window"])
        for dname, sub in [("long", sig_sw[sig_sw.direction == 1] if len(sig_sw) else sig_sw),
                           ("short", sig_sw[sig_sw.direction == -1] if len(sig_sw) else sig_sw)]:
            tr, _ = backtest(df, sub, k=2.0, risk_pct=0.03, cost_R=cfg["cost_R"], mode="scale_out")
            if len(tr) >= 20:
                dd = expectancy_decomposed(tr["net_R"].values)
                dir_rows.append({"symbol": sym, "side": dname, "n": dd["n"],
                                 "p": round(dd["p"], 3), "E": round(dd["E"], 4),
                                 "PF": round(profit_factor(tr["net_R"].values), 3)})
    grid_b_df = pd.DataFrame(grid_b)
    grid_b_df.to_csv(Rdir / "grid_breakout.csv", index=False)
    pd.DataFrame(dir_rows).to_csv(Rdir / "direction_split.csv", index=False)
    out["E2b_breakout_rows"] = len(grid_b_df)
    out["E2b_breakout_best5"] = grid_b_df.sort_values("E", ascending=False).head(5).to_dict("records") if len(grid_b_df) else []
    out["E2b_breakout_pos_frac"] = float((grid_b_df["E"] > 0).mean()) if len(grid_b_df) else 0.0
    out["E2b_direction_split"] = dir_rows
    print(f"[E2b] breakout rows={len(grid_b_df)} pos_frac={out['E2b_breakout_pos_frac']:.2f}")

    # ---------------- E3: survival / risk sweep ----------------
    # pooled live R (scale_out k=2 sweep-reclaim) as base distribution
    pool = []
    for sym, (df, src) in data.items():
        sig = signals_sweep_reclaim(df, cfg["entry_lookback"], cfg["atr_window"])
        tr, _ = backtest(df, sig, k=2.0, risk_pct=0.03, cost_R=cfg["cost_R"], mode="scale_out")
        if len(tr):
            pool.extend(tr["net_R"].tolist())
    pool = np.array(pool)
    mc_rows = []
    for rp in cfg["risk_grid"]:
        mc_rows.append(simulate(pool, rp, 200, cfg["n_montecarlo"], cfg["seed"]))
    pd.DataFrame(mc_rows).to_csv(Rdir / "montecarlo.csv", index=False)
    out["E3_montecarlo"] = mc_rows
    # Positive-expectancy control (Trader2 mechanics: p~0.5, W=1.8R, L=1.0R, net of 0.05R).
    # Shows survival at 1-3% vs ruin creep at 10% even WITH edge (validates 3%/5% caps).
    rng_pos = np.random.default_rng(cfg["seed"] + 7)
    t2pos = np.where(rng_pos.random(5000) < 0.5, rng_pos.normal(1.8, 0.3, 5000),
                     -np.abs(rng_pos.normal(1.0, 0.2, 5000))) - cfg["cost_R"]
    out["E3_Traader2_positive_control_E"] = float(np.mean(t2pos))
    mc_pos = [simulate(t2pos, rp, 200, cfg["n_montecarlo"], cfg["seed"]) for rp in cfg["risk_grid"]]
    pd.DataFrame(mc_pos).to_csv(Rdir / "montecarlo_positive.csv", index=False)
    out["E3_positive_control"] = mc_pos
    # live equity per risk% on SPY as illustration
    curves = {}
    sym0 = "SPY" if "SPY" in data else cfg["symbols"][0]
    df0, _ = data[sym0]
    sig0 = signals_sweep_reclaim(df0, cfg["entry_lookback"], cfg["atr_window"])
    for rp in [0.01, 0.03, 0.05, 0.10]:
        _, eq = backtest(df0, sig0, k=2.0, risk_pct=rp, cost_R=cfg["cost_R"], mode="scale_out")
        if len(eq):
            curves[f"risk {rp:.0%}"] = eq
    if curves:
        fig_equity(curves, Fdir / "E3_survival.png", f"{sym0} sweep-reclaim k=2 scale-out: fixed-fractional risk% (live)")
    print("[E3] montecarlo done, pool n=", len(pool))

    # ---------------- E4: scale_out vs all_in ----------------
    e4 = []
    for sym, (df, src) in data.items():
        sig = signals_sweep_reclaim(df, cfg["entry_lookback"], cfg["atr_window"])
        for k in [1.0, 2.0, 3.0]:
            tra, eqa = backtest(df, sig, k=k, risk_pct=0.03, cost_R=cfg["cost_R"], mode="all_in")
            trs, eqs = backtest(df, sig, k=k, risk_pct=0.03, cost_R=cfg["cost_R"], mode="scale_out")
            if len(tra) and len(trs):
                e4.append({"symbol": sym, "k": k,
                           "E_allin": round(expectancy_decomposed(tra["net_R"].values)["E"], 4),
                           "E_scale": round(expectancy_decomposed(trs["net_R"].values)["E"], 4),
                           "p_allin": round(expectancy_decomposed(tra["net_R"].values)["p"], 3),
                           "p_scale": round(expectancy_decomposed(trs["net_R"].values)["p"], 3)})
    pd.DataFrame(e4).to_csv(Rdir / "scale_compare.csv", index=False)
    out["E4_scale_compare"] = e4
    print(f"[E4] rows={len(e4)}")

    # ---------------- E5: pause filter (coin-flip guard) ----------------
    # simple rule: skip signal if trailing 10 closed trades win rate < 0.5
    e5 = []
    for sym, (df, src) in data.items():
        sig = signals_sweep_reclaim(df, cfg["entry_lookback"], cfg["atr_window"])
        tr, _ = backtest(df, sig, k=2.0, risk_pct=0.03, cost_R=cfg["cost_R"], mode="scale_out")
        if len(tr) < 30:
            continue
        r = tr["net_R"].values
        # simulate filter: drop trades that occur within 5 bars after a 5-loss-in-10 window
        keep = np.ones(len(r), bool)
        for i in range(10, len(r)):
            if np.mean(r[i - 10:i] > 0) < 0.5 and i < len(r) - 1:
                keep[i] = False  # stand aside one trade
        Ef, Ea = float(np.mean(r[keep])), float(np.mean(r))
        e5.append({"symbol": sym, "n": len(r), "E_all": round(Ea, 4),
                   "E_filtered": round(Ef, 4), "skipped": int(np.sum(~keep))})
    out["E5_pause_filter"] = e5
    print(f"[E5] rows={len(e5)}")

    # ---------------- E6: hidden patterns ----------------
    all_tr = []
    for sym, (df, src) in data.items():
        sig = signals_sweep_reclaim(df, cfg["entry_lookback"], cfg["atr_window"])
        tr, _ = backtest(df, sig, k=2.0, risk_pct=0.03, cost_R=cfg["cost_R"], mode="scale_out")
        if len(tr):
            t = tr.copy(); t["symbol"] = sym
            all_tr.append(t)
    if all_tr:
        big = pd.concat(all_tr, ignore_index=True)
        big.to_csv(Rdir / "trades_pooled.csv", index=False)
        R = big["net_R"].values
        dd = expectancy_decomposed(R)
        k_w = int(np.sum(R > 0))
        out["E6_pooled"] = {**dd, "PF": profit_factor(R), "ttest": expectancy_ttest(R),
                            "wilson_p": wilson_ci(k_w, len(R)),
                            "E_boot_ci": bootstrap_ci(R, cfg["n_bootstrap"], cfg["seed"]),
                            "streaks": streak_stats(R),
                            "kelly_full": kelly_fraction(dd["p"], dd["B"])}
        # MAE/MFE on SPY
        df0, _ = data[sym0]
        sig0 = signals_sweep_reclaim(df0, cfg["entry_lookback"], cfg["atr_window"])
        tr0, _ = backtest(df0, sig0, k=2.0, risk_pct=0.03, cost_R=cfg["cost_R"], mode="scale_out")
        mm = mae_mfe(df0, tr0)
        if len(mm):
            mm.to_csv(Rdir / "mae_mfe_spy.csv", index=False)
            out["E6_mae_mfe_spy"] = {"n": len(mm),
                                    "median_mfe_R": round(float(mm["mfe_R"].median()), 3),
                                    "median_realized_R": round(float(mm["realized_R"].median()), 3),
                                    "median_left_on_table_R": round(float(mm["left_on_table"].median()), 3),
                                    "mean_left_on_table_R": round(float(mm["left_on_table"].mean()), 3)}
        # cost drag: gross vs net
        out["E6_cost_drag_R"] = round(float(np.mean(big["gross_R"].values) - np.mean(big["net_R"].values)), 4)
        # decoupling scatter data saved
        print("[E6] pooled E=", round(dd["E"], 4), "n=", dd["n"])
        # scatter figure p vs E colored by B
        if len(grid_df):
            plt.figure(figsize=(8, 5))
            sc = plt.scatter(grid_df["p"], grid_df["E"], c=grid_df["B"].clip(0, 4), cmap="coolwarm")
            plt.colorbar(sc, label="B = avg_win/avg_loss")
            plt.axhline(0, color="k", lw=0.8)
            plt.xlabel("win rate p")
            plt.ylabel("expectancy E (net R/trade)")
            plt.title("Hidden pattern: win rate decoupled from expectancy (live grid)")
            plt.tight_layout(); plt.savefig(Fdir / "E6_decoupling.png", dpi=150); plt.close()

    json.dump(out, open(Rdir / "summary.json", "w"), indent=2, default=str)
    print("WROTE results/summary.json figures:", [p.name for p in Fdir.glob("*.png")])


if __name__ == "__main__":
    main()
