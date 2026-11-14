"""Veri yükleme: MT5'ten dışa aktarılan çubuk (bar) CSV'leri ve test için sentetik veri."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COLS = ["open", "high", "low", "close"]


def load_mt5_csv(path: str | Path) -> pd.DataFrame:
    """MT5 'Semboller > Çubuklar > Dışa Aktar' CSV'sini okur.

    Beklenen başlıklar: <DATE> [<TIME>] <OPEN> <HIGH> <LOW> <CLOSE> ... (sekme ya da virgül).
    Fiyatlar BID kabul edilir. Dönen indeks zaman sıralı ve tekildir.
    """
    df = pd.read_csv(path, sep=None, engine="python")
    df.columns = [c.strip().strip("<>").lower() for c in df.columns]
    if "date" not in df.columns:
        raise ValueError(f"{path}: <DATE> sütunu yok; MT5 çubuk dışa aktarımı bekleniyor")
    stamp = df["date"].astype(str).str.replace(".", "-", regex=False)
    if "time" in df.columns:
        stamp = stamp + " " + df["time"].astype(str)
    df.index = pd.to_datetime(stamp)
    df.index.name = "time"
    missing = [c for c in COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path}: eksik sütun(lar): {missing}")
    out = df[COLS].astype(float)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    validate(out, str(path))
    return out


def validate(df: pd.DataFrame, name: str = "veri") -> None:
    if df.empty:
        raise ValueError(f"{name}: boş")
    if df[COLS].isna().any().any():
        raise ValueError(f"{name}: boş (NaN) fiyat var")
    if (df[COLS] <= 0).any().any():
        raise ValueError(f"{name}: sıfır ya da negatif fiyat var")
    bad = (df["high"] < df[["open", "close", "low"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1))
    if bad.any():
        raise ValueError(f"{name}: {int(bad.sum())} çubukta high/low tutarsız (ilk: {df.index[bad][0]})")


def load_dir(directory: str | Path, symbols: list[str]) -> dict[str, pd.DataFrame]:
    """directory/<SEMBOL>.csv dosyalarını yükler (ör. EURUSD.csv, XAUUSD.csv, WTI.csv)."""
    directory = Path(directory)
    out = {}
    for s in symbols:
        p = directory / f"{s}.csv"
        if not p.exists():
            raise FileNotFoundError(f"{p} bulunamadı")
        out[s] = load_mt5_csv(p)
    return out


def synthetic(n: int = 2500, start_price: float = 1.10, vol: float = 0.006, drift: float = 0.0,
              regime_len: int = 0, regime_drift: float = 0.0, seed: int = 0,
              start: str = "2012-01-02") -> pd.DataFrame:
    """Test amaçlı günlük sentetik OHLC. GERÇEK PİYASA VERİSİ DEĞİLDİR.

    regime_len > 0 ise drift, regime_len çubukta bir +/- regime_drift arasında değişir.
    """
    rng = np.random.default_rng(seed)
    mu = np.full(n, drift)
    if regime_len > 0:
        signs = rng.choice([-1.0, 1.0], size=n // regime_len + 1)
        mu = mu + np.repeat(signs, regime_len)[:n] * regime_drift
    rets = mu + rng.normal(0.0, vol, n)
    close = start_price * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[start_price], close[:-1]]) * np.exp(rng.normal(0.0, vol * 0.1, n))
    span = np.abs(rng.normal(0.0, vol * 0.6, (2, n)))
    high = np.maximum(open_, close) * np.exp(span[0])
    low = np.minimum(open_, close) * np.exp(-span[1])
    idx = pd.bdate_range(start, periods=n, name="time")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)
