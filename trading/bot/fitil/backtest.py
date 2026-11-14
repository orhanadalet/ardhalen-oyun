"""Fitil'i geçmiş M15 verisinde çalıştırır: canlıdaki AYNI strateji ve motor kodu, simüle broker.

    python -m fitil.backtest --config config.json --data ../data [--start 2023-01-01] [--equity 10000]

Veri: --data klasöründe <SEMBOL>.csv (config'teki sembol adıyla). Desteklenen biçimler:
  - MT5 çubuk dışa aktarımı (<DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE> ...), M15 ya da M1
  - HistData M1 ASCII (YYYYMMDD HHMMSS;O;H;L;C;V), başlıksız
M1 verisi M15'e toplanır. Zamanlar UTC kabul edilir; MT5 sunucu saatindeyse --tz-shift ile düzeltin.

Simülasyonun sınırları: emirler çubuk kapanış fiyatından dolar, stoplar M15 çubuğu içinde
işlenir (çubuk içi sıralama bilinmez), spread sabittir. Sonuçlar iyimser olabilir.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .broker import PaperBroker, Spec
from .config import Config
from .engine import Engine

COLS = ["open", "high", "low", "close"]

# Simülasyon varsayılanları (brokerınızın değerleri DEĞİL; config'te sembol başına ezilebilir):
#   contract, usd_side, digits, spread (fiyat biriminde), slippage
DEFAULTS = {
    "fx":     dict(contract=100_000.0, usd_side="quote", digits=5, spread=0.00012, slippage=0.00003),
    "fx_usd": dict(contract=100_000.0, usd_side="base", digits=5, spread=0.00014, slippage=0.00003),
    "jpy":    dict(contract=100_000.0, usd_side="base", digits=3, spread=0.013, slippage=0.003),
    "gold":   dict(contract=100.0, usd_side="quote", digits=2, spread=0.30, slippage=0.05),
    "oil":    dict(contract=1000.0, usd_side="quote", digits=2, spread=0.04, slippage=0.01),
}


def kind_of(symbol: str) -> str:
    s = symbol.upper()
    if s.startswith(("XAU", "GOLD")):
        return "gold"
    if any(k in s for k in ("XTI", "XBR", "WTI", "BRENT", "OIL", "USO", "UKO", "CL")) and "USD" not in s[:3]:
        return "oil"
    if s.startswith("USD"):
        return "jpy" if "JPY" in s else "fx_usd"
    return "fx"


def sim_settings(symbol: str, entry: dict) -> dict:
    out = dict(DEFAULTS[kind_of(symbol)])
    out.update({k: entry[k] for k in ("contract", "usd_side", "digits", "spread", "slippage") if k in entry})
    return out


def load_m15(path: str | Path, tz_shift_hours: float = 0.0, minutes: int = 15) -> pd.DataFrame:
    """Çubuk dosyasını okur ve `minutes` dakikalık çubuklara getirir (daha sık veri toplanır)."""
    path = Path(path)
    first = path.read_text(encoding="utf-8", errors="ignore")[:200].splitlines()[0]
    if first.startswith(",") or first.lower().startswith(("time,", "datetime,")):     # pandas biçimi: ilk sütun zaman
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        df.columns = [c.strip().lower() for c in df.columns]
    elif "<" in first or "date" in first.lower():
        df = pd.read_csv(path, sep=None, engine="python")
        df.columns = [c.strip().strip("<>").lower() for c in df.columns]
        stamp = df["date"].astype(str).str.replace(".", "-", regex=False)
        if "time" in df.columns:
            stamp = stamp + " " + df["time"].astype(str)
        df.index = pd.to_datetime(stamp)
    else:
        df = pd.read_csv(path, sep=";", header=None, names=["stamp", "open", "high", "low", "close", "vol"])
        df.index = pd.to_datetime(df["stamp"], format="%Y%m%d %H%M%S")
    df = df[COLS].astype(float)
    df.index = df.index - pd.Timedelta(hours=tz_shift_hours)
    df.index.name = "time"
    df = df[~df.index.duplicated(keep="last")].sort_index()
    step = df.index.to_series().diff().dt.total_seconds().median()
    if step < minutes * 60:                             # daha sık veri: hedef zaman dilimine topla
        df = df.resample(f"{minutes}min", label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    elif step > minutes * 60:
        raise ValueError(f"{path}: veri {minutes} dakikadan seyrek (ortanca aralık {step:.0f} sn)")
    if (df[COLS] <= 0).any().any() or (df["high"] < df["low"]).any():
        raise ValueError(f"{path}: bozuk fiyat satırları var")
    return df


def run(data: dict[str, pd.DataFrame], cfg: Config, equity: float = 10_000.0, start=None, end=None,
        commission_per_lot: float = 7.0, log=lambda *_: None):
    """(işlemler, günlük özkaynak serisi, motor) döndürür."""
    cfg = replace(cfg, settle_sec=0.0, max_signal_age_sec=1.0)
    if end is not None:
        data = {s: d.loc[:pd.Timestamp(end)] for s, d in data.items()}
    sim = {s: sim_settings(s, cfg.symbols[s]) for s in data}
    specs = {s: Spec(s, v["digits"], 10.0 ** -v["digits"], 0.01, 0.01, 100.0) for s, v in sim.items()}
    first = max(d.index[min(len(d) - 1, cfg.strategy.min_bars)] for d in data.values())
    start = max(pd.Timestamp(start), first) if start is not None else first
    broker = PaperBroker(data, specs, {s: v["contract"] for s, v in sim.items()},
                         {s: v["usd_side"] for s, v in sim.items()}, {s: v["spread"] for s, v in sim.items()},
                         equity=equity, commission_per_lot=commission_per_lot,
                         slippage={s: v["slippage"] for s, v in sim.items()}, start=start,
                         bar=pd.Timedelta(minutes=cfg.strategy.bar_minutes).to_pytimedelta())
    engine = Engine(broker, cfg, log=log)
    days, values = [], []
    while True:
        engine.step()
        t = broker.clock
        if not days or t.normalize() != days[-1]:
            days.append(t.normalize())
            values.append(broker.account().equity)
        else:
            values[-1] = broker.account().equity
        if not broker.advance():
            break
    trades = pd.DataFrame(broker.closed)
    return trades, pd.Series(values, index=pd.DatetimeIndex(days), name="equity"), engine


def report(trades: pd.DataFrame, equity: pd.Series) -> str:
    out = []
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    total = equity.iloc[-1] / equity.iloc[0]
    rets = equity.pct_change().dropna()
    dd = (equity / equity.cummax() - 1.0).min()
    out.append(f"Dönem: {equity.index[0].date()} -> {equity.index[-1].date()} ({years:.2f} yıl)")
    out.append(f"Toplam getiri: {100 * (total - 1):.1f}%   yıllık: {100 * (total ** (1 / years) - 1):.1f}%   "
               f"azami düşüş: {100 * dd:.1f}%")
    if len(rets) > 1 and rets.std() > 0:
        out.append(f"Sharpe (günlük, yıllıklandırılmış): {rets.mean() / rets.std() * np.sqrt(252):.2f}")
    if trades.empty:
        out.append("İşlem yok.")
        return "\n".join(out)
    pnl, wins, losses = trades["pnl"], trades.loc[trades["pnl"] > 0, "pnl"], trades.loc[trades["pnl"] <= 0, "pnl"]
    per_day = trades.groupby(pd.to_datetime(trades["open_time"]).dt.date).size()
    trading_days = max(1, int((equity.index.weekday < 5).sum()))
    streak = worst = 0
    for x in pnl:
        streak = streak + 1 if x <= 0 else 0
        worst = max(worst, streak)
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
    out.append(f"İşlem: {len(trades)}   günde ortalama: {len(trades) / trading_days:.1f}   en yoğun gün: {per_day.max()}")
    out.append(f"Kazanma oranı: {100 * len(wins) / len(trades):.1f}%   profit factor: {pf:.2f}   "
               f"ortalama R: {trades['r'].mean():.3f}   en uzun kayıp serisi: {worst}")
    out.append(f"Ortalama kazanç: {trades.loc[trades['r'] > 0, 'r'].mean():.2f}R   ortalama kayıp: "
               f"{trades.loc[trades['r'] <= 0, 'r'].mean():.2f}R   en iyi: {trades['r'].max():.2f}R   en kötü: {trades['r'].min():.2f}R")

    def table(key, title):
        g = trades.groupby(key)
        t = pd.DataFrame({"islem": g.size(), "net": g["pnl"].sum().round(2), "ort_R": g["r"].mean().round(3),
                          "kazanma_%": (100 * g["pnl"].apply(lambda x: (x > 0).mean())).round(1)})
        out.append(f"\n{title}:\n{t.to_string()}")
    table("setup", "Kuruluma göre")
    table("symbol", "Sembole göre")
    table("reason", "Çıkış nedenine göre")
    table(pd.to_datetime(trades["open_time"]).dt.hour.rename("saat_utc"), "Giriş saatine göre (UTC)")
    monthly = equity.groupby([equity.index.year, equity.index.month]).agg(["first", "last"])
    m = (100 * (monthly["last"] / monthly["first"] - 1)).round(1)
    out.append(f"\nAylık getiri (%): pozitif ay {int((m > 0).sum())}/{len(m)}, en kötü {m.min()}, en iyi {m.max()}")
    return "\n".join(out)


def _score(trades: pd.DataFrame, min_trades: int) -> float:
    """Parametre seçimi için ölçüt: ortalama R (yeterli işlem yoksa -sonsuz)."""
    if len(trades) < min_trades:
        return float("-inf")
    return float(trades["r"].mean())


def walk_forward(data: dict[str, pd.DataFrame], cfg: Config, grid: dict[str, list], train_days: int = 120,
                 test_days: int = 30, min_trades: int = 30, equity: float = 10_000.0):
    """Strateji parametrelerini geçmiş pencerede seçer, HİÇ GÖRÜLMEMİŞ sonraki pencerede sınar.

    grid: StrategyParams alan adı -> denenecek değerler. Dönüş: (dilim tablosu, tüm örnek dışı işlemler).
    Yalnızca örnek dışı (test) sonuçlar stratejinin kanıtı sayılır; eğitim sonuçları iyimserdir.
    """
    from itertools import product
    keys = list(grid)
    combos = [dict(zip(keys, v)) for v in product(*[grid[k] for k in keys])]
    first = max(d.index[min(len(d) - 1, cfg.strategy.min_bars)] for d in data.values())
    last = min(d.index[-1] for d in data.values())
    folds, oos = [], []
    t0 = first
    while t0 + pd.Timedelta(days=train_days + test_days) <= last:
        tr_end = t0 + pd.Timedelta(days=train_days)
        te_end = tr_end + pd.Timedelta(days=test_days)
        scored = []
        for c in combos:
            trades, _, _ = run(data, replace(cfg, strategy=replace(cfg.strategy, **c)), equity, t0, tr_end)
            scored.append((_score(trades, min_trades), c))
        best_score, best = max(scored, key=lambda x: x[0])
        chosen = best if np.isfinite(best_score) else {}
        trades, _, _ = run(data, replace(cfg, strategy=replace(cfg.strategy, **chosen)), equity, tr_end, te_end)
        folds.append(dict(egitim=f"{t0.date()}..{tr_end.date()}", test=f"{tr_end.date()}..{te_end.date()}",
                          secilen=chosen, egitim_ort_R=round(best_score, 3) if np.isfinite(best_score) else None,
                          test_islem=len(trades), test_ort_R=round(float(trades["r"].mean()), 3) if len(trades) else None))
        if len(trades):
            oos.append(trades)
        t0 = t0 + pd.Timedelta(days=test_days)
    if not folds:
        raise ValueError("Walk-forward için veri yetersiz (eğitim + test penceresi sığmıyor)")
    return pd.DataFrame(folds), (pd.concat(oos, ignore_index=True) if oos else pd.DataFrame())


WF_GRID = {"setups": [("sweep", "pullback"), ("sweep",), ("pullback",)], "max_stop_atr": [2.0, 3.0]}


def main() -> None:
    ap = argparse.ArgumentParser(prog="fitil.backtest")
    ap.add_argument("--config", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--tz-shift", type=float, default=0.0, help="verinin UTC'den farkı (saat); MT5 sunucu saati için ör. 2")
    ap.add_argument("--trades-out", help="işlem listesini CSV olarak yaz")
    ap.add_argument("--wf", action="store_true", help="walk-forward: parametreyi geçmişte seç, görülmemiş dönemde sına")
    ap.add_argument("--train-days", type=int, default=120)
    ap.add_argument("--test-days", type=int, default=30)
    a = ap.parse_args()
    cfg = Config.load(a.config)
    data = {}
    for s in cfg.symbols:
        p = Path(a.data) / f"{s}.csv"
        if p.exists():
            data[s] = load_m15(p, a.tz_shift, cfg.strategy.bar_minutes)
            print(f"{s}: {len(data[s])} çubuk, {data[s].index[0]} -> {data[s].index[-1]}")
        else:
            print(f"{s}: {p} yok, atlandı")
    if not data:
        raise SystemExit("Hiç veri dosyası bulunamadı")
    cfg = replace(cfg, symbols={s: cfg.symbols[s] for s in data})
    if a.wf:
        folds, oos = walk_forward(data, cfg, WF_GRID, a.train_days, a.test_days, equity=a.equity)
        pd.set_option("display.width", 200)
        print()
        print(folds.to_string(index=False))
        if len(oos):
            se = oos["r"].std() / np.sqrt(len(oos))
            print(f"\nÖrnek dışı toplam: {len(oos)} işlem, ortalama R {oos['r'].mean():+.3f} (standart hata {se:.3f}), "
                  f"kazanma {100 * (oos['pnl'] > 0).mean():.1f}%")
            print("Ortalama R, standart hatanın iki katından büyük değilse sonuç şanstan ayırt edilemez.")
        return
    trades, equity, _ = run(data, cfg, a.equity, a.start, a.end)
    print()
    print(report(trades, equity))
    if a.trades_out and len(trades):
        trades.to_csv(a.trades_out, index=False)
        print(f"\nİşlemler yazıldı: {a.trades_out}")


if __name__ == "__main__":
    main()
