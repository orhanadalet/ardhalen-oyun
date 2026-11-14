"""Performans ölçütleri. Tek sayıya değil, hepsine birlikte bakın."""
from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(equity: pd.Series, trades: pd.DataFrame, periods_per_year: int = 252) -> dict:
    rets = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0]
    dd = equity / equity.cummax() - 1.0
    vol = rets.std() * np.sqrt(periods_per_year) if len(rets) > 1 else np.nan
    out = {
        "yil": round(years, 2),
        "toplam_getiri": total - 1.0,
        "yillik_getiri": total ** (1 / years) - 1.0 if total > 0 else -1.0,
        "yillik_oynaklik": vol,
        "sharpe": rets.mean() / rets.std() * np.sqrt(periods_per_year) if len(rets) > 1 and rets.std() > 0 else np.nan,
        "azami_dusus": dd.min(),
        "islem_sayisi": int(len(trades)),
    }
    if len(trades):
        pnl = trades["pnl"]
        wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
        streak = best = 0
        for x in pnl:
            streak = streak + 1 if x <= 0 else 0
            best = max(best, streak)
        out.update({
            "kazanma_orani": len(wins) / len(pnl),
            "profit_factor": wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf,
            "ortalama_R": trades["r"].mean(),
            "en_uzun_kayip_serisi": best,
            "toplam_maliyet": trades["costs"].sum(),
        })
    return out


def by_symbol(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby("symbol")
    return pd.DataFrame({"islem": g.size(), "net_kz": g["pnl"].sum(), "ortalama_R": g["r"].mean(),
                         "kazanma_orani": g["pnl"].apply(lambda x: (x > 0).mean())})


def by_year(equity: pd.Series) -> pd.Series:
    y = equity.groupby(equity.index.year).agg(["first", "last"])
    return (y["last"] / y["first"] - 1.0).rename("yillik_getiri")
