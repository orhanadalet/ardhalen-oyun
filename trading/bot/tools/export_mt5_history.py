"""MT5'ten fiyat geçmişini dışa aktarır: Fitil'in backtest'i için hazır, tek bir zip dosyası.

Windows'ta, MT5 terminali AÇIK ve hesaba giriş yapılmışken, bot klasöründe:

    python tools/export_mt5_history.py --config config.json

Yaptığı:
  - config.json'daki her sembolün M15 (istenirse --timeframe 1 ile M1) çubuklarını terminalin verebildiği en eski
    tarihten bugüne okur. Emir göndermez, hiçbir şeyi değiştirmez.
  - Zamanları UTC'ye çevirir (config'teki server_time ayarı ya da canlı fiyattan otomatik kestirim).
  - Her çubuğun gerçek spread'ini de yazar; backtest bu spread'i kullanabilir.
  - Sembol özelliklerini (hane, lot adımı, kontrat büyüklüğü, en az stop mesafesi) ve Londra/New York saatlerindeki
    ortanca spread'i ozet.json dosyasına yazar.
  - Hepsini fitil_veri_<tarih>.zip dosyasında toplar. Hesap numarası, şifre ya da bakiye YAZILMAZ.

Daha uzun geçmiş için MT5'te: Araçlar > Seçenekler > Grafikler > "Grafikteki en fazla çubuk" = Sınırsız,
sonra terminali yeniden başlatın. Brokerın sunucusunda olmayan geçmiş indirilemez.
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fitil.config import Config            # noqa: E402
from fitil.servertime import describe, detect_server_clock   # noqa: E402

CHUNK = 50_000


def read_all(mt5, symbol: str, timeframe: int, max_bars: int) -> np.ndarray:
    """Kapanmış çubukları yeniden eskiye parça parça okur (pozisyon 0 = oluşan çubuk, alınmaz)."""
    parts, pos = [], 1
    while pos <= max_bars:
        chunk = mt5.copy_rates_from_pos(symbol, timeframe, pos, min(CHUNK, max_bars - pos + 1))
        if chunk is None or len(chunk) == 0:
            break
        parts.append(chunk)
        if len(chunk) < CHUNK:
            break
        pos += len(chunk)
    if not parts:
        return np.zeros(0)
    return np.concatenate(parts[::-1])


def resolve_server_clock(cfg: Config, mt5, minutes: int, max_bars: int) -> tuple[Config, str]:
    """Config'te sunucu saati yoksa fiyat geçmişinden çıkarır (piyasa kapalıyken de çalışır)."""
    from dataclasses import replace
    if cfg.server_time is not None or cfg.server_utc_offset_hours is not None:
        return cfg, "config'ten"
    if not mt5.initialize():
        raise RuntimeError(f"MT5 başlatılamadı: {mt5.last_error()}")
    ref = (["EURUSD"] if "EURUSD" in cfg.symbols else []) + list(cfg.symbols)   # döviz kapanışı en net referans
    tf = mt5.TIMEFRAME_H1 if minutes == 60 else getattr(mt5, f"TIMEFRAME_M{minutes}")
    for sym in dict.fromkeys(ref):
        if mt5.symbol_info(sym) is None:
            continue
        mt5.symbol_select(sym, True)
        print(f"Sunucu saati {sym} geçmişinden çıkarılıyor (ilk çalıştırmada birkaç dakika sürebilir)...", flush=True)
        rates = read_all(mt5, sym, tf, max_bars)
        if len(rates) == 0:
            continue
        clock = detect_server_clock(pd.to_datetime(rates["time"], unit="s"), minutes)
        if clock is not None:
            new = replace(cfg, server_time={"tz": clock["tz"], "plus_hours": clock["plus_hours"]}) if "tz" in clock \
                else replace(cfg, server_utc_offset_hours=float(clock["offset_hours"]))
            return new, f"{sym} geçmişinden çıkarıldı: {describe(clock)}"
    return cfg, "geçmişten çıkarılamadı, canlı fiyattan kestirilecek"


def export(cfg: Config, out_dir: Path, minutes: int = 15, max_bars: int = 2_000_000, mt5=None, broker=None,
           years: float = 0.0, per_symbol: bool = False) -> list[Path]:
    from fitil.mt5_broker import MT5Broker
    if broker is None:
        if mt5 is None:
            import MetaTrader5 as mt5                                  # noqa: N813  (yalnızca Windows)
        cfg, how = resolve_server_clock(cfg, mt5, minutes, max_bars)
        print(f"Sunucu saati: {how}")
        if cfg.server_time is not None:
            print(f'  Bot için config.json\'a ekleyin: "server_time": {json.dumps(cfg.server_time)}')
        elif cfg.server_utc_offset_hours is not None:
            print(f'  Bot için config.json\'a ekleyin: "server_utc_offset_hours": {cfg.server_utc_offset_hours:g}')
        broker = MT5Broker(cfg, allow_real=True, mt5=mt5)              # yalnızca okuma: emir gönderilmez
    mt5 = broker.mt5
    tf = mt5.TIMEFRAME_H1 if minutes == 60 else getattr(mt5, f"TIMEFRAME_M{minutes}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    written: list[Path] = []
    groups = [[s] for s in cfg.symbols] if per_symbol else [list(cfg.symbols)]
    for group in groups:
        suffix = f"_{group[0]}" if per_symbol else ""
        zpath = out_dir / f"fitil_veri_{stamp}{suffix}.zip"
        _write_zip(broker, mt5, tf, zpath, group, stamp, minutes, max_bars, years)
        written.append(zpath)
    return written


def _write_zip(broker, mt5, tf, zpath: Path, symbols: list[str], stamp: str, minutes: int, max_bars: int,
               years: float) -> None:
    clock = broker.cfg.server_time or ({"offset_hours": broker.cfg.server_utc_offset_hours}
                                       if broker.cfg.server_utc_offset_hours is not None else {"otomatik": True})
    summary = {"olusturma_utc": stamp, "zaman_dilimi_dk": minutes, "sunucu_saati": clock, "semboller": {}}
    with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for sym in symbols:
            info = mt5.symbol_info(sym)
            print(f"{sym:10s} okunuyor...", end="\r", flush=True)
            rates = read_all(mt5, sym, tf, max_bars)
            if len(rates) == 0:
                print(f"{sym:10s} veri yok (Market Watch'ta açık mı?)")
                summary["semboller"][sym] = {"hata": "veri yok"}
                continue
            df = pd.DataFrame(rates)
            idx = broker._utc_index(df["time"].to_numpy())
            out = pd.DataFrame({"open": df["open"], "high": df["high"], "low": df["low"], "close": df["close"],
                                "volume": df["tick_volume"], "spread_points": df["spread"]})
            out.index = idx
            out.index.name = "time"
            out = out[~out.index.duplicated(keep="last")].sort_index()
            if years > 0:
                out = out.loc[out.index >= out.index[-1] - pd.Timedelta(days=365.25 * years)]
            z.writestr(f"{sym}.csv", out.to_csv(float_format="%.10g"))
            point = float(info.point)
            hours = out.index.hour
            active = out.loc[(hours >= 7) & (hours < 17), "spread_points"]
            med = float(active.median()) if len(active) else float("nan")
            summary["semboller"][sym] = {
                "cubuk": int(len(out)), "ilk_utc": str(out.index[0]), "son_utc": str(out.index[-1]),
                "hane": int(info.digits), "point": point, "kontrat": float(getattr(info, "trade_contract_size", 0.0)),
                "lot_en_az": float(info.volume_min), "lot_adim": float(info.volume_step),
                "en_az_stop_point": int(info.trade_stops_level),
                "ortanca_spread_point_07_17_utc": med, "ortanca_spread_fiyat_07_17_utc": med * point,
            }
            print(f"{sym:10s} {len(out):8d} çubuk  {out.index[0]:%Y-%m-%d} -> {out.index[-1]:%Y-%m-%d}  "
                  f"ortanca spread (07-17 UTC): {med:g} point = {med * point:.5g}")
        z.writestr("ozet.json", json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    from fitil.__main__ import use_utf8_output
    use_utf8_output()
    ap = argparse.ArgumentParser(prog="export_mt5_history")
    ap.add_argument("--config", required=True)
    ap.add_argument("--timeframe", type=int, default=15, choices=(1, 5, 15, 30, 60), help="dakika (varsayılan 15)")
    ap.add_argument("--out", default=".", help="zip dosyasının yazılacağı klasör")
    ap.add_argument("--years", type=float, default=0.0, help="yalnızca son N yıl (varsayılan: hepsi)")
    ap.add_argument("--per-symbol", action="store_true",
                    help="her sembol için ayrı zip (GitHub'a tarayıcıdan yüklerken dosya başına 25 MB sınırı için)")
    a = ap.parse_args()
    try:
        cfg = Config.load(a.config)
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError, OSError) as e:
        raise SystemExit(f"Yapılandırma hatası ({a.config}): {e}")
    try:
        paths = export(cfg, Path(a.out), a.timeframe, years=a.years, per_symbol=a.per_symbol)
    except ImportError:
        raise SystemExit("MetaTrader5 paketi yok. Windows'ta: pip install -r requirements.txt")
    except RuntimeError as e:
        raise SystemExit(f"Dışa aktarılamadı: {e}")
    finally:
        try:
            import MetaTrader5
            MetaTrader5.shutdown()
        except Exception:
            pass
    print()
    for path in paths:
        size = path.stat().st_size / 1e6
        warn = "  <- 25 MB'tan büyük: --per-symbol ya da --years ile küçültün" if size > 25 else ""
        print(f"Hazır: {path.resolve()}  ({size:.1f} MB){warn}")


if __name__ == "__main__":
    main()
