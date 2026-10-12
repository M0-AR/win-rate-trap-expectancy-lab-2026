"""Live public market data loader.

Priority (best practice for public reproducibility):
1. Yahoo Finance via yfinance (public, no key) for symbols in config.
2. Stooq daily CSV fallback (no key): https://stooq.com/q/d/l/?s=spy.us&d1=...&d2=...&i=d
3. Deterministic synthetic GBM (seeded) so the pipeline never crashes offline;
   flagged clearly as synthetic in outputs.

All frames normalized to columns: date, open, high, low, close, volume.
"""
from __future__ import annotations
import io
import time
from pathlib import Path
import numpy as np
import pandas as pd


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    # yfinance multiindex flatten
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    rename = {"adj close": "adjclose", "adj_close": "adjclose"}
    df = df.rename(columns=rename)
    for c in ["open", "high", "low", "close"]:
        if c not in df.columns:
            raise ValueError(f"missing OHLC col {c}")
    if "volume" not in df.columns:
        df["volume"] = 0
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def fetch_yahoo(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        import yfinance as yf
        df = yf.download(symbol, start=start, end=end, auto_adjust=False,
                         progress=False, repair=True, timeout=30)
        if df is None or len(df) < 100:
            return None
        return _normalize(df)
    except Exception as e:
        print(f"[data] yahoo fail {symbol}: {e}")
        return None


def fetch_stooq(symbol: str, start: str, end: str) -> pd.DataFrame | None:
    try:
        mapping = {"SPY": "spy.us", "QQQ": "qqq.us", "DIA": "dia.us", "IWM": "iwm.us",
                   "CL=F": "cl.f", "GC=F": "gc.f", "BTC-USD": "btcusd"}
        st = mapping.get(symbol, symbol.lower())
        d1 = start.replace("-", "")
        d2 = end.replace("-", "")
        url = f"https://stooq.com/q/d/l/?s={st}&d1={d1}&d2={d2}&i=d"
        df = pd.read_csv(url, parse_dates=["Date"], index_col="Date")
        if len(df) < 100:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df[["open", "high", "low", "close", "volume"]]
    except Exception as e:
        print(f"[data] stooq fail {symbol}: {e}")
        return None


def synthetic(symbol: str, start: str, end: str, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(symbol)) % (2**31) + seed)
    idx = pd.bdate_range(start=start, end=end)
    n = len(idx)
    drift, vol = 0.0004, 0.012
    rets = rng.normal(drift, vol, n)
    close = 100 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[100], close[:-1]]) * (1 + rng.normal(0, 0.001, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, n)))
    volume = rng.integers(1_000_000, 10_000_000, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": volume}, index=idx)


def load_all(symbols: list[str], start: str, end: str, data_dir: str = "data",
             seed: int = 42, sleep_s: float = 1.0) -> dict:
    out: dict = {}
    d = Path(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    for s in symbols:
        cache = d / f"{s.replace('=','').replace('-','_')}.csv"
        df, source = None, "cache"
        if cache.exists():
            try:
                df = pd.read_csv(cache, parse_dates=[0], index_col=0)
                if len(df) > 100:
                    print(f"[data] {s}: cache {len(df)} rows")
                    out[s] = (df, "cache")
                    continue
            except Exception:
                df = None
        for fn, name in [(fetch_yahoo, "yahoo"), (fetch_stooq, "stooq")]:
            df = fn(s, start, end)
            if df is not None and len(df) > 100:
                source = name
                break
            time.sleep(sleep_s)
        if df is None or len(df) < 100:
            df, source = synthetic(s, start, end, seed), "synthetic(fallback)"
        df.to_csv(cache)
        print(f"[data] {s}: {source} {len(df)} rows {df.index[0].date()}->{df.index[-1].date()}")
        out[s] = (df, source)
    return out
