"""Fixed-fractional backtest with R accounting and scale-out (video mechanics).

Execution model (daily bars, conservative):
- Enter at next open after signal (no lookahead).
- Risk per trade = risk_pct * equity_at_entry (fixed fractional, same size every
  trade; scale-ins capped so total <= risk_pct, per video 3% default).
- Shares = risk_budget / risk_abs.
- Two exits tested:
    * all_in: full position to kR target or -1R stop (first touch on high/low).
    * scale_out (video): half at +1R, move stop to breakeven, rest to kR.
      Realized R ~= 0.5*1 + 0.5*k on full win, 0.5*1 + 0.5*0 on breakeven-runner,
      -1 on stop. Net R subtracts cost_R friction.
- One position at a time; overlapping signals skipped (conservative, auditable).

Returns trades DataFrame with gross_R, net_R, pnl, equity curve.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def backtest(df: pd.DataFrame, signals: pd.DataFrame, k: float = 2.0,
             risk_pct: float = 0.03, initial_equity: float = 10000.0,
             cost_R: float = 0.05, mode: str = "scale_out",
             max_bars: int = 20) -> tuple[pd.DataFrame, pd.Series]:
    px = df.copy()
    equity = initial_equity
    trades = []
    eq_curve = []
    busy_until = None
    sig = signals.set_index("date").sort_index() if len(signals) else signals

    dates = px.index
    for t in range(len(dates) - 1):
        today = dates[t]
        nxt = dates[t + 1]
        if busy_until is not None and nxt <= busy_until:
            eq_curve.append((nxt, equity))
            continue
        if len(signals) == 0 or today not in sig.index:
            eq_curve.append((nxt, equity))
            continue
        rows = sig.loc[[today]] if today in sig.index else None
        if rows is None:
            eq_curve.append((nxt, equity))
            continue
        row = rows.iloc[0] if isinstance(rows, pd.DataFrame) else rows
        direction = int(row["direction"])
        risk_abs = float(row["risk_abs"])
        if not np.isfinite(risk_abs) or risk_abs <= 0:
            eq_curve.append((nxt, equity))
            continue
        entry = float(px.loc[nxt, "open"])
        # re-anchor stop/target to actual fill so R stays exact
        if direction == 1:
            stop = entry - risk_abs
            t1, tk = entry + risk_abs, entry + k * risk_abs
        else:
            stop = entry + risk_abs
            t1, tk = entry - risk_abs, entry - k * risk_abs
        risk_budget = risk_pct * equity
        shares = risk_budget / risk_abs
        # walk forward max_bars
        gross_R, exit_date = -1.0, nxt
        hit_t1 = False
        for b in range(1, max_bars + 1):
            if t + b >= len(dates):
                break
            d = dates[t + b]
            hi, lo = float(px.loc[d, "high"]), float(px.loc[d, "low"])
            if direction == 1:
                stop_hit = lo <= stop
                t1_hit = hi >= t1
                tk_hit = hi >= tk
            else:
                stop_hit = hi >= stop
                t1_hit = lo <= t1
                tk_hit = lo <= tk
            if mode == "all_in":
                if tk_hit and not stop_hit:
                    gross_R, exit_date = k, d
                    break
                if stop_hit:
                    gross_R, exit_date = -1.0, d
                    break
            else:  # scale_out
                if not hit_t1 and t1_hit and not stop_hit:
                    hit_t1 = True
                    stop = entry  # breakeven
                    continue
                if not hit_t1 and stop_hit:
                    gross_R, exit_date = -1.0, d
                    break
                if hit_t1:
                    if tk_hit and not stop_hit:
                        gross_R, exit_date = 0.5 * 1.0 + 0.5 * k, d
                        break
                    if stop_hit:  # breakeven scratch on runner
                        gross_R, exit_date = 0.5 * 1.0 + 0.5 * 0.0, d
                        break
        else:
            # timed exit: mark to market in R
            last = float(px.loc[dates[min(t + max_bars, len(dates) - 1)], "close"])
            mtm = (last - entry) * direction / risk_abs
            gross_R = float(np.clip(mtm, -1.0, k))
            exit_date = dates[min(t + max_bars, len(dates) - 1)]
        net_R = gross_R - cost_R
        pnl = net_R * risk_budget
        equity = max(equity + pnl, 1.0)
        busy_until = exit_date
        trades.append({"entry_date": nxt, "exit_date": exit_date, "direction": direction,
                       "entry": entry, "risk_abs": risk_abs, "risk_pct": risk_pct,
                       "gross_R": gross_R, "net_R": net_R, "pnl": pnl, "equity": equity})
        eq_curve.append((exit_date, equity))
    trades_df = pd.DataFrame(trades)
    eq = pd.Series(dict(eq_curve)).sort_index() if eq_curve else pd.Series([initial_equity])
    return trades_df, eq
