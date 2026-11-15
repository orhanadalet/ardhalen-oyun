"""Haftalık rapor: canlı (ya da demo) sonuçları, risk merdiveni, düşüş, müdahaleler.

    python -m fitil.report [--workdir .] [--expected-r 0.14]

Botun yazdığı fitil_state.json ve fitil_journal.csv dosyalarını okur; hiçbir şeyi değiştirmez.
--expected-r: backtest'te ölçülen işlem başı beklenti; canlı sonuç bununla karşılaştırılır.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path

import numpy as np


def load(workdir: Path) -> tuple[dict, list[dict]]:
    state = json.loads((workdir / "fitil_state.json").read_text(encoding="utf-8-sig"))
    journal = []
    jp = workdir / "fitil_journal.csv"
    if jp.exists():
        with jp.open(encoding="utf-8-sig", newline="") as f:
            journal = list(csv.DictReader(f))
    return state, journal


def build(state: dict, journal: list[dict], expected_r: float | None = None) -> str:
    out = []
    res = [x for x in state.get("results", []) if x.get("r") is not None and np.isfinite(x["r"])]
    r = np.array([x["r"] for x in res], dtype=float)
    n = len(r)
    out.append("FİTİL RAPORU")
    out.append("=" * 60)
    if n == 0:
        out.append("Henüz kapanmış işlem yok.")
    else:
        se = float(r.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
        wins, losses = r[r > 0], r[r <= 0]
        out.append(f"Kapanmış işlem: {n}   kazanma: {100 * len(wins) / n:.1f}%   toplam: {r.sum():+.1f}R")
        out.append(f"İşlem başı beklenti: {r.mean():+.3f}R (std. hata {se:.3f})")
        if len(wins) and len(losses):
            out.append(f"Ortalama kazanç: {wins.mean():.2f}R   ortalama kayıp: {-losses.mean():.2f}R   "
                       f"başabaş kazanma oranı: {100 * -losses.mean() / (wins.mean() - losses.mean()):.1f}%")
        if expected_r is not None and n > 1:
            z = (r.mean() - expected_r) / se
            verdict = ("backtest ile uyumlu" if abs(z) < 2 else
                       "backtest'ten BELİRGİN KÖTÜ: stratejiyi gözden geçirin" if z < 0 else "backtest'ten iyi")
            out.append(f"Backtest beklentisi {expected_r:+.3f}R ile fark: {r.mean() - expected_r:+.3f}R (z = {z:+.1f}) -> {verdict}")
        streak = worst = 0
        for x in r:
            streak = streak + 1 if x <= 0 else 0
            worst = max(worst, streak)
        out.append(f"En uzun kayıp serisi: {worst}")
        weeks: dict[str, list[float]] = {}
        for x in res:
            y, w, _ = datetime.fromisoformat(x["t"]).isocalendar()
            weeks.setdefault(f"{y}-H{w:02d}", []).append(x["r"])
        out.append("Son haftalar: " + "  ".join(f"{k}: {sum(v):+.1f}R ({len(v)})" for k, v in list(weeks.items())[-6:]))
    out.append("")
    ladder = state.get("ladder", 0.5)
    out.append(f"Risk merdiveni: {ladder}x   " + {
        0.5: "(ilk 100 işlem ya da son dönem zayıf; 100+ işlemde beklenti pozitifse 1x)",
        1.0: "(300+ işlemde beklenti bir standart hata farkla pozitifse 2x)",
        2.0: "(en üst kademe)"}.get(ladder, ""))
    peak, adj = state.get("peak_equity", 0.0), state.get("adj", 1.0)
    out.append(f"Zirve (akıştan arındırılmış): {peak:,.2f}   ayar çarpanı: {adj:.4f}")
    out.append("")
    kinds: dict[str, list[float]] = {}
    for x in res:
        kinds.setdefault(x["kind"], []).append(x["r"])
    if kinds:
        out.append("Kapanış türüne göre: " + "  ".join(f"{k}: {len(v)} işlem, {np.mean(v):+.2f}R" for k, v in kinds.items()))
    manual = [row for row in journal if row.get("event") == "MÜDAHALE"]
    closes = sum(1 for x in res if x["kind"] == "elle")
    stops = len(manual) - closes if len(manual) >= closes else 0
    out.append(f"İnsan müdahalesi: {len(manual)} (elle kapatma: {closes}, stop değiştirme: {stops})")
    if n and closes:
        own = [x["r"] for x in res if x["kind"] == "elle"]
        out.append(f"  Elle kapatılan işlemlerin ortalaması: {np.mean(own):+.2f}R. Sistemin kendi çıkışlarıyla "
                   "karşılaştırın; erken kâr almak gerçek veride sistemi zarara çevirdi.")
    blocks = [row for row in journal if row.get("event") == "ENGEL"]
    if blocks:
        out.append(f"Giriş engelleri: {len(blocks)} (son: {blocks[-1]['detail']})")
    hints = [row for row in journal if row.get("event") == "ÇEKİM"]
    if hints:
        out.append(f"Çekim önerileri: {len(hints)} (son: {hints[-1]['detail']})")
    return "\n".join(out)


def main() -> None:
    from .__main__ import use_utf8_output
    use_utf8_output()
    ap = argparse.ArgumentParser(prog="fitil.report")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--expected-r", type=float, help="backtest'teki işlem başı beklenti (R)")
    a = ap.parse_args()
    try:
        state, journal = load(Path(a.workdir))
    except FileNotFoundError:
        raise SystemExit(f"{a.workdir} içinde fitil_state.json yok: bot bu klasörde henüz çalışmamış")
    print(build(state, journal, a.expected_r))


if __name__ == "__main__":
    main()
