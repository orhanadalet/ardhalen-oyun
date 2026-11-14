"""Kullanım:  python -m fxlab --data ../data --symbols EURUSD GBPUSD XAUUSD WTI [--wf]"""
from __future__ import annotations

import argparse

import pandas as pd

from .backtest import Params, run
from .data import load_dir
from .instruments import default_universe
from .metrics import by_symbol, by_year, summarize
from .walkforward import walk_forward


def main() -> None:
    ap = argparse.ArgumentParser(prog="fxlab")
    ap.add_argument("--data", required=True, help="<SEMBOL>.csv dosyalarının klasörü (MT5 dışa aktarımı)")
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--risk", type=float, default=Params().risk_per_trade)
    ap.add_argument("--wf", action="store_true", help="walk-forward çalıştır")
    a = ap.parse_args()

    universe = default_universe()
    unknown = [s for s in a.symbols if s not in universe]
    if unknown:
        raise SystemExit(f"Tanımsız sembol(ler): {unknown}. Tanımlılar: {sorted(universe)}")
    data = load_dir(a.data, a.symbols)
    params = Params(risk_per_trade=a.risk)
    pd.set_option("display.width", 160)

    if a.wf:
        grid = {"stop_atr": [2.0, 3.0, 4.0], "lookbacks": [(20, 60, 120), (40, 80, 160), (10, 30, 90)]}
        folds, equity = walk_forward(data, universe, grid, params)
        print(folds.to_string(index=False))
        print("\nBirleşik örnek dışı (OOS) sonuç:")
        m = summarize(equity, pd.DataFrame(columns=["pnl", "r", "costs"]))
        m.pop("islem_sayisi")          # işlem sayıları yukarıdaki dilim tablosunda
    else:
        res = run(data, universe, params, a.start, a.end)
        m = summarize(res.equity, res.trades)
        print(by_symbol(res.trades).to_string(), "\n")
        print(by_year(res.equity).to_string(), "\n")
        print("Atlanan girişler:", res.skipped)
    for k, v in m.items():
        print(f"{k:>22}: {v:.4f}" if isinstance(v, float) else f"{k:>22}: {v}")


if __name__ == "__main__":
    main()
