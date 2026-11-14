"""Sinyal mantığı. Saf fonksiyon: kapanmış M15 çubukları girer, sinyal ya da None çıkar.

İki kurulum:
  1) Likidite süpürmesi (sahte kırılım; config'te setups ile açılır): fiyat önceki gün tepe/dibini ya da Asya aralığını
     fitille aşar, çubuk geri içeride ve güçlü kapanır -> süpürmenin tersine işlem.
  2) Trend geri çekilmesi: H1 ve H4 aynı yönde, fiyat M15 EMA'sına çekilip trend yönünde
     önceki çubuğun ucunu kırarak kapanır.
Stop her zaman yapının ötesindedir; sabit kâr hedefi yoktur (çıkışı engine yönetir).

DURUM: gerçek veride sınandı (bkz. bot/README.md). Süpürme hiçbir veride kazandırmadı ve varsayılan olarak
kapalıdır; geri çekilme yalnızca EURUSD'nin bir döneminde kazandırdı. Güvenilir avantaj gösterilmiş değildir.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import StrategyParams


@dataclass(frozen=True)
class Signal:
    direction: int        # +1 al, -1 sat
    stop: float           # stop-loss fiyatı
    score: int            # kanaat puanı 1..3 (risk kademesini belirler)
    setup: str            # "sweep" | "pullback"
    reason: str


def _ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def atr(h: np.ndarray, l: np.ndarray, c: np.ndarray, n: int) -> float:
    pc = c[-n - 1:-1]
    tr = np.maximum(h[-n:] - l[-n:], np.maximum(np.abs(h[-n:] - pc), np.abs(l[-n:] - pc)))
    return float(tr.mean())


def htf_bias(df: pd.DataFrame, p: StrategyParams) -> int:
    """+1: fiyat hem H1 hem H4 EMA'sının üstünde, -1: ikisinin altında, 0: karışık/veri az."""
    last = float(df["close"].iloc[-1])
    out = []
    for rule, n in (("1h", p.h1_ema), ("4h", p.h4_ema)):
        closes = df["close"].resample(rule).last().dropna()
        if len(closes) < n + 5:
            return 0
        out.append(np.sign(last - _ema(closes.to_numpy(), n)[-1]))
    return int(out[0]) if out[0] == out[1] else 0


def liquidity_levels(df: pd.DataFrame, p: StrategyParams) -> dict[str, float]:
    """Son çubuğun gününe göre: önceki gün tepe/dip ve (Asya bittiyse) Asya aralığı."""
    t = df.index[-1]
    day = t.normalize()
    levels: dict[str, float] = {}
    prev = df.loc[df.index < day]
    for _ in range(5):                       # son TAM işlem günü: pazar akşamının birkaç çubuğu sayılmaz
        if not len(prev):
            break
        d0 = prev.index[-1].normalize()
        pday = prev.loc[prev.index >= d0]
        if len(pday) * p.bar_minutes >= p.min_day_hours * 60:
            levels["pdh"], levels["pdl"] = float(pday["high"].max()), float(pday["low"].min())
            break
        prev = prev.loc[prev.index < d0]
    if t.hour >= p.asia_end_hour:
        asia = df.loc[(df.index >= day) & (df.index < day + pd.Timedelta(hours=p.asia_end_hour))]
        if len(asia) * p.bar_minutes >= p.min_asia_hours * 60:
            levels["asia_h"], levels["asia_l"] = float(asia["high"].max()), float(asia["low"].min())
    return levels


def evaluate(df: pd.DataFrame, p: StrategyParams, spread: float) -> Signal | None:
    """df: UTC indeksli, yalnızca KAPANMIŞ çubuklar (open/high/low/close), p.bar_minutes zaman diliminde."""
    if len(df) < max(p.min_bars, p.atr_period + 2, p.pullback_ema_slow + 5):
        return None
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    a = atr(h, l, c, p.atr_period)
    rng = h[-1] - l[-1]
    if not np.isfinite(a) or a <= 0 or rng <= 0:
        return None
    bias = htf_bias(df, p)
    strong = rng >= p.strong_range_atr * a

    def finish(direction: int, stop: float, score: int, setup: str, reason: str) -> Signal | None:
        entry = c[-1] + (spread if direction > 0 else 0.0)
        dist = (entry - stop) * direction
        if dist <= 0 or dist > p.max_stop_atr * a or dist < p.min_stop_spread_mult * spread:
            return None
        return Signal(direction, float(stop), int(min(3, score)), setup, reason)

    # --- 1) Likidite süpürmesi ---
    if "sweep" in p.setups and rng >= p.sweep_min_range_atr * a:
        n = p.sweep_fresh_bars
        levels = liquidity_levels(df, p)
        for name in ("pdl", "asia_l"):
            lv = levels.get(name)
            if lv is None:
                continue
            if (l[-1] < lv < c[-1] and c[-1] > o[-1] and (c[-1] - l[-1]) >= p.sweep_close_frac * rng
                    and (c[-n - 1:-1] > lv).all()):
                score = 1 + (bias > 0) + strong - (bias < 0)
                if score >= 1:
                    return finish(+1, l[-1] - p.stop_buffer_atr * a, score, "sweep", f"{name} süpürüldü")
        for name in ("pdh", "asia_h"):
            lv = levels.get(name)
            if lv is None:
                continue
            if (h[-1] > lv > c[-1] and c[-1] < o[-1] and (h[-1] - c[-1]) >= p.sweep_close_frac * rng
                    and (c[-n - 1:-1] < lv).all()):
                score = 1 + (bias < 0) + strong - (bias > 0)
                if score >= 1:
                    return finish(-1, h[-1] + p.stop_buffer_atr * a, score, "sweep", f"{name} süpürüldü")

    # --- 2) Trend geri çekilmesi ---
    # Kanaat puanı sabit 2 (standart risk). Geniş giriş çubuğuna ek risk verilmez: dört gerçek veri
    # setinde (554 işlem) geniş çubuklu girişler daha iyi çıkmadı (-0,03R'ye karşı +0,04R).
    if "pullback" in p.setups and bias != 0:
        fast, slow = _ema(c, p.pullback_ema_fast), _ema(c, p.pullback_ema_slow)
        k, s = p.pullback_touch_bars, p.pullback_swing_bars
        if bias > 0 and fast[-1] > slow[-1] and c[-1] > o[-1] and c[-1] > h[-2] and c[-1] > fast[-1] \
                and (l[-k:] <= fast[-k:]).any():
            return finish(+1, l[-s:].min() - p.stop_buffer_atr * a, 2, "pullback", "EMA geri çekilmesi, yukarı")
        if bias < 0 and fast[-1] < slow[-1] and c[-1] < o[-1] and c[-1] < l[-2] and c[-1] < fast[-1] \
                and (h[-k:] >= fast[-k:]).any():
            return finish(-1, h[-s:].max() + p.stop_buffer_atr * a, 2, "pullback", "EMA geri çekilmesi, aşağı")
    return None
