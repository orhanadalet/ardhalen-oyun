"""Walk-forward: parametreyi geçmiş pencerede seç, hiç görülmemiş sonraki pencerede sına.

Raporlanacak sonuç yalnızca birleştirilmiş ÖRNEK DIŞI (OOS) eğridir.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import product

import numpy as np
import pandas as pd

from .backtest import Params, run
from .metrics import summarize


def _objective(res, min_trades: int) -> float:
    m = summarize(res.equity, res.trades)
    if m["islem_sayisi"] < min_trades or not np.isfinite(m.get("sharpe", np.nan)):
        return -np.inf
    return m["sharpe"]


def walk_forward(data, universe, grid: dict[str, list], base: Params = Params(),
                 train_years: float = 4.0, test_years: float = 1.0, min_trades: int = 30):
    start = max(df.index[0] for df in data.values())
    end = min(df.index[-1] for df in data.values())
    keys = list(grid)
    combos = [dict(zip(keys, v)) for v in product(*[grid[k] for k in keys])]
    train, test = pd.Timedelta(days=365.25 * train_years), pd.Timedelta(days=365.25 * test_years)

    folds, pieces = [], []
    t0 = start
    while t0 + train + test <= end + pd.Timedelta(days=1):
        tr_end, te_end = t0 + train, t0 + train + test
        scored = [(_objective(run(data, universe, replace(base, **c), start=t0, end=tr_end), min_trades), c)
                  for c in combos]
        best_score, best = max(scored, key=lambda x: x[0])
        if not np.isfinite(best_score):
            best = {}            # yeterli işlem yok: varsayılan parametrelerle devam
        oos = run(data, universe, replace(base, **best), start=tr_end + pd.Timedelta(days=1), end=te_end)
        m = summarize(oos.equity, oos.trades)
        folds.append(dict(egitim_bas=t0.date(), test_bas=tr_end.date(), test_bit=te_end.date(),
                          secilen=best, egitim_sharpe=best_score, test_sharpe=m.get("sharpe"),
                          test_getiri=m["toplam_getiri"], test_islem=m["islem_sayisi"]))
        pieces.append(oos.equity.pct_change().fillna(oos.equity.iloc[0] / base.initial_equity - 1.0))
        t0 = t0 + test
    if not pieces:
        raise ValueError("Walk-forward için veri yetersiz (egitim+test penceresi sığmıyor)")
    rets = pd.concat(pieces)
    equity = base.initial_equity * (1.0 + rets).cumprod()
    return pd.DataFrame(folds), equity
