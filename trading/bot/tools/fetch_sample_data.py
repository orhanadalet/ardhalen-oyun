"""Açık kaynak paketlerin içindeki gerçek EURUSD örnek verisini indirir (sınama için).

    python tools/fetch_sample_data.py [hedef_klasör]        # varsayılan: ../data

İndirilenler (dosyalar yalnızca açılır, paketlerin kodu çalıştırılmaz; sha256 doğrulanır):
  EURUSD_M1_2019.csv   EURUSD dakikalık, Mart 2019 - Nisan 2020  (fxsim 0.0.24, Dukascopy dışa aktarımı)
  EURUSD_H1_2017.csv   EURUSD saatlik, 2017                        (gym-anytrading 2.0.0)
  EURUSD_H1_2017b.csv  EURUSD saatlik, Nisan 2017 - Şubat 2018     (backtesting 0.6.6)

Sonra, örneğin M15 sınaması için dakikalık dosyayı EURUSD.csv adıyla kopyalayın:
    python -m fitil.backtest --config config.json --data ../data
Veriler ilgili paketlerin dağıtımından gelir; kendi brokerınızın verisi değildir.
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

SOURCES = [
    ("fxsim", "0.0.24", "fxsim-0.0.24.tar.gz",
     "ace93ea14eaeb06c5f8b4f3b855c38a2701d82c22c9d71880c3e8d3aac41de56",
     "fxsim-0.0.24/fxsim/EURUSD.csv", "EURUSD_M1_2019.csv"),
    ("gym-anytrading", "2.0.0", "gym_anytrading-2.0.0-py3-none-any.whl",
     "29e88b9d38272ea338c8cfcebc6a0f0302c7a8c02048331ad6a4d22cdd825e54",
     "gym_anytrading/datasets/data/FOREX_EURUSD_1H_ASK.csv", "EURUSD_H1_2017.csv"),
    ("backtesting", "0.6.6", "backtesting-0.6.6-py3-none-any.whl",
     "d2fc29ab6e10fbf44b05570720cef144d6d0d2ec8e183977114f2be9c5089b78",
     "backtesting/test/EURUSD.csv", "EURUSD_H1_2017b.csv"),
]


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "data")
    target.mkdir(parents=True, exist_ok=True)
    for name, version, filename, sha, member, out in SOURCES:
        meta = json.loads(get(f"https://pypi.org/pypi/{name}/{version}/json"))
        url = next(u["url"] for u in meta["urls"] if u["filename"] == filename)
        blob = get(url)
        if hashlib.sha256(blob).hexdigest() != sha:
            raise SystemExit(f"{filename}: sha256 uyuşmuyor, dosya kullanılmadı")
        if filename.endswith(".whl"):
            data = zipfile.ZipFile(io.BytesIO(blob)).read(member)
        else:
            data = tarfile.open(fileobj=io.BytesIO(blob)).extractfile(member).read()
        (target / out).write_bytes(data)
        print(f"{out:22s} {len(data) / 1e6:6.1f} MB  ({name} {version})")


if __name__ == "__main__":
    main()
