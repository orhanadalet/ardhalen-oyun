"""Canlı çalıştırma (Windows + MT5):

    python -m fitil --config config.json              # sinyalleri yazar, EMİR GÖNDERMEZ
    python -m fitil --config config.json --trade      # demo hesapta emir gönderir
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .broker import FatalError
from .config import Config
from .engine import Engine


def acquire_lock(path: Path):
    """Aynı klasörde ikinci bir Fitil'in çalışmasını engeller. Dosya tutamacı açık kaldıkça kilit durur."""
    f = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise SystemExit(f"Fitil bu klasörde zaten çalışıyor ({path}). İkinci kopya başlatılmadı.")
    return f


def main() -> None:
    ap = argparse.ArgumentParser(prog="fitil")
    ap.add_argument("--config", required=True)
    ap.add_argument("--trade", action="store_true", help="emir gönder (yoksa yalnızca sinyal yazar)")
    ap.add_argument("--allow-real", action="store_true", help="gerçek hesapta çalışmaya izin ver")
    ap.add_argument("--workdir", default=".", help="durum ve günlük dosyalarının klasörü")
    ap.add_argument("--reset-peak", action="store_true",
                    help="düşüş frenini sıfırla (zirve = şimdiki özkaynak). %%20 durmasından sonra, nedeni inceledikten sonra kullanın")
    a = ap.parse_args()

    try:
        cfg = Config.load(a.config)
    except FileNotFoundError:
        raise SystemExit(f"Yapılandırma dosyası bulunamadı: {a.config}  (config.example.json dosyasını kopyalayın)")
    except (ValueError, TypeError, KeyError) as e:
        raise SystemExit(f"Yapılandırma hatası ({a.config}): {e}")
    work = Path(a.workdir)
    work.mkdir(parents=True, exist_ok=True)
    lock = acquire_lock(work / "fitil.lock")       # süreç boyunca açık kalmalı
    broker = None
    try:
        try:
            from .mt5_broker import MT5Broker
            broker = MT5Broker(cfg, allow_real=a.allow_real)
        except ImportError:
            raise SystemExit("MetaTrader5 paketi yok. Windows'ta: pip install -r requirements.txt")
        except (FatalError, RuntimeError) as e:
            raise SystemExit(f"Fitil başlatılamadı: {e}")
        if a.trade and not broker.trading_enabled():
            raise SystemExit("MT5'te 'Algo Trading' kapalı. Araç çubuğundaki düğmeyi etkinleştirip yeniden başlatın.")
        acct = broker.account()
        mode = "EMİR GÖNDERİR" if a.trade else "yalnızca sinyal (dry-run)"
        print(f"Hesap: {'DEMO' if acct.is_demo else 'GERÇEK'}  özkaynak={acct.equity:.2f} {acct.currency}  mod: {mode}")
        print(f"Semboller: {', '.join(cfg.symbols)}  zaman dilimi: M{cfg.strategy.bar_minutes}  "
              f"kurulumlar: {', '.join(cfg.strategy.setups)}")
        engine = Engine(broker, cfg, state_path=work / "fitil_state.json", journal_path=work / "fitil_journal.csv",
                        dry_run=not a.trade)
        if a.reset_peak:
            engine.reset_peak(acct.equity)
            print(f"Düşüş freni sıfırlandı: zirve = {acct.equity:.2f}")
        try:
            engine.run()
        except KeyboardInterrupt:
            print("Durduruldu. Açık pozisyonlar ve stopları brokerda duruyor.")
        except FatalError as e:
            print(f"FİTİL DURDU: {e}\nAçık pozisyonlar ve stopları brokerda duruyor.")
    finally:
        if broker is not None:
            broker.shutdown()
        lock.close()

if __name__ == "__main__":
    main()
