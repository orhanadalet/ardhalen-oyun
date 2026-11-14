"""FRED'den günlük serileri indirir:  python fetch_fred.py [hedef_klasör]

Ağ ayarında fred.stlouisfed.org izinli olmalıdır. Dosyalar <SEMBOL>.fred.csv adıyla yazılır.
"""
import sys
import time
import urllib.request
from pathlib import Path

from fxlab.data import FRED_SERIES

target = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "data")
target.mkdir(parents=True, exist_ok=True)
for symbol, series in FRED_SERIES.items():
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
    with urllib.request.urlopen(url, timeout=60) as r:
        body = r.read()
    (target / f"{symbol}.fred.csv").write_bytes(body)
    print(f"{symbol:7s} {series:13s} {len(body):>8d} bayt")
    time.sleep(1)
