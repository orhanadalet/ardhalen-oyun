"""Canlı çalıştırma (Windows + MT5):

    python -m ardbot --config config.json              # sinyalleri yazar, EMİR GÖNDERMEZ
    python -m ardbot --config config.json --trade      # demo hesapta emir gönderir
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .config import Config
from .engine import Engine


def main() -> None:
    ap = argparse.ArgumentParser(prog="ardbot")
    ap.add_argument("--config", required=True)
    ap.add_argument("--trade", action="store_true", help="emir gönder (yoksa yalnızca sinyal yazar)")
    ap.add_argument("--allow-real", action="store_true", help="gerçek hesapta çalışmaya izin ver")
    ap.add_argument("--workdir", default=".", help="durum ve günlük dosyalarının klasörü")
    a = ap.parse_args()

    cfg = Config.load(a.config)
    from .mt5_broker import MT5Broker
    broker = MT5Broker(cfg, allow_real=a.allow_real)
    acct = broker.account()
    mode = "EMİR GÖNDERİR" if a.trade else "yalnızca sinyal (dry-run)"
    print(f"Hesap: {'DEMO' if acct.is_demo else 'GERÇEK'}  özkaynak={acct.equity:.2f} {acct.currency}  mod: {mode}")
    work = Path(a.workdir)
    engine = Engine(broker, cfg, state_path=work / "ardbot_state.json", journal_path=work / "ardbot_journal.csv",
                    dry_run=not a.trade)
    try:
        engine.run()
    except KeyboardInterrupt:
        print("Durduruldu. Açık pozisyonlar ve stopları brokerda duruyor.")
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
