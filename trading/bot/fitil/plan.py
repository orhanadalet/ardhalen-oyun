"""Beklenti hesaplayıcı: verilen işlem istatistikleriyle Fitil'in risk kuralları altında
bir yılın nasıl geçebileceğini Monte Carlo ile gösterir.

    python -m fitil.plan --equity 1000 --win-rate 0.45 --avg-win 1.8 --avg-loss 1.0 --trades-per-day 6

Bu bir TAHMİN DEĞİL, bir "eğer" hesabıdır: kazanma oranı ve ortalama kazanç/kayıp sizin
girdinizdir (backtest ya da demo sonuçlarından alın). Araç yalnızca o istatistikler gerçekse
risk kurallarının sonucu nasıl şekillendirdiğini gösterir. İşlemler bağımsız varsayılır;
gerçekte kayıplar kümelenir, yani gerçek düşüşler burada görünenden derin olabilir.
"""
from __future__ import annotations

import argparse

import numpy as np

from .config import Config


def simulate(equity: float, win_rate: float, avg_win: float, avg_loss: float, trades_per_day: float,
             cfg: Config | None = None, risk: float | None = None, cost_r: float = 0.0, weeks: int = 52,
             paths: int = 4000, seed: int = 0) -> dict:
    """R cinsinden sonuçlarla (kazanç +avg_win, kayıp -avg_loss, her işlemde -cost_r) simülasyon."""
    cfg = cfg or Config(symbols={"X": {"usd_beta": 0}})
    risk = risk if risk is not None else cfg.risk_tiers.get(2, 0.005)
    rng = np.random.default_rng(seed)
    eq = np.full(paths, float(equity))
    peak = eq.copy()
    base = eq.copy()                      # çekim için zirve tabanı
    withdrawn = np.zeros(paths)
    max_dd = np.zeros(paths)
    halted = np.zeros(paths, dtype=bool)
    weekly = []
    for _ in range(weeks):
        for _day in range(5):
            day_start = eq.copy()
            n = rng.poisson(trades_per_day, paths)
            for k in range(int(n.max()) if paths else 0):
                dd = 1.0 - eq / peak
                factor = np.where(dd >= cfg.dd_halt, 0.0, np.where(dd >= cfg.dd_half_risk, 0.5, 1.0))
                halted |= factor == 0.0
                active = (k < n) & ((day_start - eq) / day_start < cfg.daily_loss_limit) & (factor > 0)
                win = rng.random(paths) < win_rate
                r = np.where(win, avg_win, -avg_loss) - cost_r
                eq = eq + np.where(active, eq * risk * factor * r, 0.0)
                peak = np.maximum(peak, eq)
                max_dd = np.maximum(max_dd, 1.0 - eq / peak)
        gain = np.maximum(0.0, eq - base)
        take = gain * cfg.withdraw_frac
        eq = eq - take
        base = np.where(take > 0, eq, base)
        peak = np.where(take > 0, peak * np.where(eq + take > 0, eq / (eq + take), 1.0), peak)
        withdrawn += take
        weekly.append(take)
    total = eq + withdrawn
    q = lambda x, p: float(np.percentile(x, p))
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss - cost_r
    return {
        "beklenti_R": expectancy,
        "yil_sonu_toplam_p5": q(total, 5), "yil_sonu_toplam_medyan": q(total, 50), "yil_sonu_toplam_p95": q(total, 95),
        "haftalik_cekim_medyan": float(np.median(np.mean(weekly, axis=0))),
        "toplam_cekim_medyan": q(withdrawn, 50),
        "azami_dusus_medyan": q(max_dd, 50), "azami_dusus_p95": q(max_dd, 95),
        "durma_olasiligi": float(halted.mean()),
        "zararda_bitirme_olasiligi": float((total < equity).mean()),
    }


def required_equity(weekly_target: float, **kw) -> float:
    """Medyan haftalık çekimin hedefe ulaşması için gereken yaklaşık başlangıç sermayesi (oransal ölçekleme)."""
    per_unit = simulate(1.0, **kw)["haftalik_cekim_medyan"]
    return float("inf") if per_unit <= 0 else weekly_target / per_unit


def main() -> None:
    ap = argparse.ArgumentParser(prog="fitil.plan")
    ap.add_argument("--equity", type=float, required=True)
    ap.add_argument("--win-rate", type=float, required=True, help="0-1 arası, ör. 0.45")
    ap.add_argument("--avg-win", type=float, required=True, help="ortalama kazanç, R cinsinden")
    ap.add_argument("--avg-loss", type=float, default=1.0, help="ortalama kayıp, R cinsinden")
    ap.add_argument("--trades-per-day", type=float, required=True)
    ap.add_argument("--risk", type=float, help="işlem başına risk (kesir); varsayılan orta kademe")
    ap.add_argument("--cost-r", type=float, default=0.0, help="istatistiklere dahil değilse işlem başı maliyet (R)")
    ap.add_argument("--weekly-target", type=float, help="haftalık çekim hedefi; gereken sermayeyi de hesaplar")
    ap.add_argument("--config")
    a = ap.parse_args()
    cfg = Config.load(a.config) if a.config else None
    kw = dict(win_rate=a.win_rate, avg_win=a.avg_win, avg_loss=a.avg_loss, trades_per_day=a.trades_per_day,
              cfg=cfg, risk=a.risk, cost_r=a.cost_r)
    r = simulate(a.equity, **kw)
    print(f"İşlem başına beklenti: {r['beklenti_R']:+.3f} R")
    if r["beklenti_R"] <= 0:
        print("Beklenti sıfır ya da negatif: bu istatistiklerle hiçbir risk ayarı para kazandırmaz.")
    print(f"Bir yıl sonra toplam (hesap + çekilen):  kötü %5: {r['yil_sonu_toplam_p5']:.0f}   "
          f"medyan: {r['yil_sonu_toplam_medyan']:.0f}   iyi %5: {r['yil_sonu_toplam_p95']:.0f}")
    print(f"Haftalık çekim (medyan yol): {r['haftalik_cekim_medyan']:.2f}   yıllık toplam çekim: {r['toplam_cekim_medyan']:.0f}")
    print(f"Azami düşüş: medyan {100 * r['azami_dusus_medyan']:.1f}%   kötü %5: {100 * r['azami_dusus_p95']:.1f}%")
    print(f"%{100 * (cfg.dd_halt if cfg else 0.20):.0f} düşüşle durma olasılığı: {100 * r['durma_olasiligi']:.1f}%   "
          f"yılı zararda bitirme olasılığı: {100 * r['zararda_bitirme_olasiligi']:.1f}%")
    if a.weekly_target:
        need = required_equity(a.weekly_target, **kw)
        print(f"Haftada {a.weekly_target:.0f} çekim için gereken yaklaşık sermaye: "
              + ("ulaşılamaz (beklenti pozitif değil)" if not np.isfinite(need) else f"{need:,.0f}"))
    print("\nNot: girdiler sizin varsayımınızdır; işlemler bağımsız sayılmıştır. Gerçek sonuç daha kötü olabilir.")


if __name__ == "__main__":
    main()
