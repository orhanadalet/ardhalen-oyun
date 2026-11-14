"""Sinyal: çok dönemli Donchian kırılım topluluğu (trend takibi) + ATR.

Tüm hesaplar yalnızca KAPANMIŞ çubukları kullanır; t anındaki değer t ve öncesine bakar.
MQL5 tarafındaki Signals.mqh ile aynı tanımlar kullanılır.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    """True Range'in basit ortalaması (MT5 iATR ile aynı tanım)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def donchian_state(df: pd.DataFrame, n: int) -> pd.Series:
    """+1: kapanış önceki n çubuğun en yükseğini aştı; -1: en düşüğünü kırdı; aksi halde önceki durum."""
    upper = df["high"].rolling(n).max().shift(1)
    lower = df["low"].rolling(n).min().shift(1)
    raw = pd.Series(np.nan, index=df.index)
    raw[df["close"] > upper] = 1.0
    raw[df["close"] < lower] = -1.0
    return raw.ffill().fillna(0.0)


def ensemble_score(df: pd.DataFrame, lookbacks: tuple[int, ...]) -> pd.Series:
    """[-1, +1] aralığında skor: lookback'lerin durum ortalaması."""
    return sum(donchian_state(df, n) for n in lookbacks) / len(lookbacks)
