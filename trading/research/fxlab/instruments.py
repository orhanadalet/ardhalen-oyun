"""Enstrüman tanımları.

Buradaki maliyet ve kontrat değerleri YAYGIN VARSAYIMLARDIR, brokerınızın gerçek
değerleri değildir. Gerçek test öncesi MT5 > Sembol > Özellikler ekranındaki
değerlerle güncelleyin (spread, komisyon, swap, kontrat büyüklüğü).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math


@dataclass(frozen=True)
class Instrument:
    symbol: str
    contract_size: float          # 1 lotun büyüklüğü (baz birim / ons / varil)
    usd_side: str                 # "quote": XXXUSD (K/Z USD), "base": USDXXX (K/Z karşı para)
    usd_beta: int                 # sembolde LONG olmak USD'de: -1 short, +1 long, 0 nötr
    spread: float                 # fiyat biriminde ortalama spread
    commission_per_lot: float     # USD, gidiş-dönüş, lot başına
    swap_long: float = 0.0        # USD / lot / gece (negatif = maliyet)
    swap_short: float = 0.0
    slippage: float = 0.0         # fiyat biriminde, stop/piyasa emri başına
    volume_min: float = 0.01
    volume_step: float = 0.01
    volume_max: float = 100.0

    def usd_per_price_unit(self, price: float) -> float:
        """1 lot için fiyat 1.0 birim oynayınca USD cinsinden K/Z."""
        if self.usd_side == "quote":
            return self.contract_size
        return self.contract_size / price

    def pnl_usd(self, lots: float, direction: int, entry: float, exit_: float) -> float:
        return direction * lots * (exit_ - entry) * self.usd_per_price_unit(exit_)

    def round_lots(self, lots: float) -> float:
        """Adıma AŞAĞI yuvarlar; minimumun altındaysa 0 döner (risk büyütülmez)."""
        if not math.isfinite(lots) or lots <= 0:
            return 0.0
        stepped = math.floor(lots / self.volume_step + 1e-9) * self.volume_step
        if stepped < self.volume_min - 1e-12:
            return 0.0
        return round(min(stepped, self.volume_max), 8)

    def with_costs(self, **kw) -> "Instrument":
        return replace(self, **kw)


def default_universe() -> dict[str, Instrument]:
    fx = dict(contract_size=100_000.0, commission_per_lot=7.0)
    return {
        "EURUSD": Instrument("EURUSD", usd_side="quote", usd_beta=-1, spread=0.00010, slippage=0.00005, swap_long=-6.0, swap_short=1.5, **fx),
        "GBPUSD": Instrument("GBPUSD", usd_side="quote", usd_beta=-1, spread=0.00014, slippage=0.00006, swap_long=-3.0, swap_short=-1.5, **fx),
        "AUDUSD": Instrument("AUDUSD", usd_side="quote", usd_beta=-1, spread=0.00012, slippage=0.00005, swap_long=-2.5, swap_short=-0.5, **fx),
        "NZDUSD": Instrument("NZDUSD", usd_side="quote", usd_beta=-1, spread=0.00016, slippage=0.00006, swap_long=-2.0, swap_short=-1.0, **fx),
        "USDJPY": Instrument("USDJPY", usd_side="base", usd_beta=+1, spread=0.012, slippage=0.005, swap_long=8.0, swap_short=-16.0, **fx),
        "USDCHF": Instrument("USDCHF", usd_side="base", usd_beta=+1, spread=0.00015, slippage=0.00006, swap_long=5.0, swap_short=-11.0, **fx),
        "USDCAD": Instrument("USDCAD", usd_side="base", usd_beta=+1, spread=0.00016, slippage=0.00006, swap_long=1.0, swap_short=-5.0, **fx),
        "XAUUSD": Instrument("XAUUSD", contract_size=100.0, usd_side="quote", usd_beta=-1, spread=0.30, slippage=0.10, commission_per_lot=7.0, swap_long=-35.0, swap_short=15.0),
        "WTI": Instrument("WTI", contract_size=1000.0, usd_side="quote", usd_beta=0, spread=0.03, slippage=0.02, commission_per_lot=7.0, swap_long=-8.0, swap_short=-4.0),
        "BRENT": Instrument("BRENT", contract_size=1000.0, usd_side="quote", usd_beta=0, spread=0.04, slippage=0.02, commission_per_lot=7.0, swap_long=-8.0, swap_short=-4.0),
    }
