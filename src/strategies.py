"""Signal generators that enforce the video's mechanics on real OHLC.

Two rule-based long/short engines (no ML, no lookahead):

A) sweep_reclaim (the video's crude-oil example):
   long when today's low sweeps prior N-day low (low < rolling min) but close
   reclaims above that swept level within the same bar. Short mirrored.
   Stop = swept extreme +/- buffer; buffer = 0.25*ATR so risk is structural.
   Target = entry +/- k*Risk (k configurable). This is price-based R, not size.

B) breakout_ATR (trend control):
   long on 20-day high breakout, short on 20-day low breakdown,
   stop = 1.0*ATR, target = k*Risk.

Both emit: entry_date, entry_price, direction (+1/-1), stop, risk_abs, target_k.
Position sizing (fixed fractional, Tharp/BBX 1% rule + video 2-3%/5%) is applied
in backtest.py, never inside the signal.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def signals_sweep_reclaim(df: pd.DataFrame, lookback: int = 20,
                          atr_window: int = 14, buffer_mult: float = 0.25) -> pd.DataFrame:
    a = atr(df, atr_window)
    roll_low = df["low"].shift(1).rolling(lookback).min()
    roll_high = df["high"].shift(1).rolling(lookback).max()
    sigs = []
    for i in range(lookback + 1, len(df)):
        row = df.iloc[i]
        swept_low = row["low"] < roll_low.iloc[i]
        reclaimed = row["close"] > roll_low.iloc[i]
        swept_high = row["high"] > roll_high.iloc[i]
        lost = row["close"] < roll_high.iloc[i]
        buf = buffer_mult * a.iloc[i]
        if swept_low and reclaimed:
            entry = row["close"]
            stop = min(row["low"], roll_low.iloc[i]) - buf
            risk = entry - stop
            if risk > 0:
                sigs.append((df.index[i], entry, 1, stop, risk))
        elif swept_high and lost:
            entry = row["close"]
            stop = max(row["high"], roll_high.iloc[i]) + buf
            risk = stop - entry
            if risk > 0:
                sigs.append((df.index[i], entry, -1, stop, risk))
    return pd.DataFrame(sigs, columns=["date", "entry", "direction", "stop", "risk_abs"])


def signals_breakout(df: pd.DataFrame, lookback: int = 20,
                     atr_window: int = 14, atr_mult: float = 1.0) -> pd.DataFrame:
    a = atr(df, atr_window)
    roll_high = df["high"].shift(1).rolling(lookback).max()
    roll_low = df["low"].shift(1).rolling(lookback).min()
    sigs = []
    for i in range(lookback + 1, len(df)):
        row = df.iloc[i]
        if row["close"] > roll_high.iloc[i]:
            risk = atr_mult * a.iloc[i]
            sigs.append((df.index[i], row["close"], 1, row["close"] - risk, risk))
        elif row["close"] < roll_low.iloc[i]:
            risk = atr_mult * a.iloc[i]
            sigs.append((df.index[i], row["close"], -1, row["close"] + risk, risk))
    return pd.DataFrame(sigs, columns=["date", "entry", "direction", "stop", "risk_abs"])
