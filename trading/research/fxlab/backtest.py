"""Portföy backtest motoru (çubuk tabanlı).

Kurallar:
- Sinyal t çubuğunun KAPANIŞINDA hesaplanır, emir t+1 çubuğunun AÇILIŞINDA gerçekleşir.
- Veri BID kabul edilir: long giriş ask'tan (bid+spread), short çıkış ask'tan.
- Stop çubuk içinde tetiklenir; açılış boşlukla stopu geçtiyse açılıştan dolar (kötü fiyat).
- Pozisyon büyüklüğü: özkaynak * risk% / (stop mesafesi * lot değeri), adıma aşağı yuvarlanır.
- Portföy sınırları: toplam açık risk ve net USD yönü riski.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .instruments import Instrument
from .strategy import atr, ensemble_score


@dataclass(frozen=True)
class Params:
    lookbacks: tuple[int, ...] = (20, 60, 120)
    entry_threshold: float = 0.66      # |skor| bu değere ulaşınca giriş
    atr_period: int = 20
    stop_atr: float = 3.0              # başlangıç ve iz süren stop mesafesi (ATR katı)
    risk_per_trade: float = 0.005      # özkaynağın oranı
    max_portfolio_risk: float = 0.04   # tüm açık pozisyonların stopa kadar toplam riski
    max_usd_net_risk: float = 0.02     # tek USD yönünde net risk
    initial_equity: float = 100_000.0


@dataclass
class Position:
    symbol: str
    direction: int
    lots: float
    entry: float
    stop: float
    entry_time: pd.Timestamp
    initial_risk: float
    costs: float = 0.0
    last_time: pd.Timestamp | None = None


@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    params: Params
    skipped: dict = field(default_factory=dict)


def _risk_to_stop(pos: Position, inst: Instrument, price: float) -> float:
    """Fiyat şu an stop'a giderse kaybedilecek USD (kâr kilitlendiyse 0)."""
    loss = -inst.pnl_usd(pos.lots, pos.direction, pos.entry, pos.stop)
    return max(0.0, loss)


def run(data: dict[str, pd.DataFrame], universe: dict[str, Instrument], params: Params = Params(),
        start=None, end=None) -> Result:
    p = params
    ind = {}
    for s, df in data.items():
        ind[s] = pd.DataFrame({"atr": atr(df, p.atr_period), "score": ensemble_score(df, p.lookbacks)})
    index = sorted(set().union(*[df.index for df in data.values()]))
    if start is not None:
        index = [t for t in index if t >= pd.Timestamp(start)]
    if end is not None:
        index = [t for t in index if t <= pd.Timestamp(end)]
    if not index:
        raise ValueError("Seçilen aralıkta veri yok")

    balance = p.initial_equity
    positions: dict[str, Position] = {}
    pending_exit: set[str] = set()
    pending_entry: dict[str, tuple[int, float]] = {}   # sembol -> (yön, sinyal anındaki ATR)
    last_close: dict[str, float] = {}
    trades: list[dict] = []
    curve: list[float] = []
    skipped = {"min_lot": 0, "portfolio_cap": 0, "usd_cap": 0}

    def unrealized() -> float:
        return sum(universe[s].pnl_usd(q.lots, q.direction, q.entry, last_close[s]) for s, q in positions.items())

    def close(sym: str, bid_price: float, t, reason: str) -> None:
        nonlocal balance
        pos = positions.pop(sym)
        inst = universe[sym]
        px = bid_price if pos.direction > 0 else bid_price + inst.spread
        gross = inst.pnl_usd(pos.lots, pos.direction, pos.entry, px)
        balance += gross
        net = gross - pos.costs
        trades.append(dict(symbol=sym, direction=pos.direction, lots=pos.lots, entry_time=pos.entry_time,
                           exit_time=t, entry=pos.entry, exit=px, pnl=net, costs=pos.costs,
                           r=net / pos.initial_risk if pos.initial_risk > 0 else np.nan, reason=reason))

    for t in index:
        bars = {s: data[s].loc[t] for s in data if t in data[s].index}

        # 1) Bekleyen çıkışlar: açılıştan
        for s in [s for s in pending_exit if s in bars and s in positions]:
            close(s, bars[s]["open"], t, "sinyal")
            pending_exit.discard(s)

        # 2) Gece taşıma maliyeti (swap)
        for s, pos in positions.items():
            if s in bars and pos.last_time is not None:
                nights = max(0, (t.normalize() - pos.last_time.normalize()).days)
                inst = universe[s]
                swap = (inst.swap_long if pos.direction > 0 else inst.swap_short) * pos.lots * nights
                balance += swap
                pos.costs -= swap
            if s in bars:
                pos.last_time = t

        # 3) Bekleyen girişler: açılıştan, portföy sınırları dahilinde
        for s in [s for s in pending_entry if s in bars and s not in positions]:
            direction, a = pending_entry.pop(s)
            inst = universe[s]
            o = bars[s]["open"]
            entry = o + inst.spread + inst.slippage if direction > 0 else o - inst.slippage
            dist = p.stop_atr * a
            stop = entry - direction * dist
            equity = balance + unrealized()
            per_lot = dist * inst.usd_per_price_unit(entry)
            budget = equity * p.risk_per_trade
            open_risk = sum(_risk_to_stop(q, universe[k], last_close[k]) for k, q in positions.items())
            room = equity * p.max_portfolio_risk - open_risk
            if room < budget:
                skipped["portfolio_cap"] += 1
                budget = max(0.0, room)
            lots = inst.round_lots(budget / per_lot) if per_lot > 0 else 0.0
            if lots <= 0:
                skipped["min_lot"] += 1
                continue
            risk = lots * per_lot
            usd_dir = direction * inst.usd_beta
            if usd_dir != 0:
                net = sum(_risk_to_stop(q, universe[k], last_close[k]) * q.direction * universe[k].usd_beta
                          for k, q in positions.items())
                if abs(net + usd_dir * risk) > equity * p.max_usd_net_risk and abs(net + usd_dir * risk) > abs(net):
                    skipped["usd_cap"] += 1
                    continue
            commission = inst.commission_per_lot * lots
            balance -= commission
            positions[s] = Position(s, direction, lots, entry, stop, t, risk, costs=commission, last_time=t)
            last_close.setdefault(s, o)
        for s in list(pending_entry):
            if s in bars:
                pending_entry.pop(s, None)

        # 4) Stop kontrolü (aynı çubukta açılan pozisyonlar dahil)
        for s in [s for s in positions if s in bars]:
            pos, inst, b = positions[s], universe[s], bars[s]
            if pos.direction > 0:
                if b["low"] <= pos.stop:
                    fill = min(pos.stop, b["open"]) - inst.slippage
                    close(s, fill, t, "stop")
            else:
                if b["high"] + inst.spread >= pos.stop:
                    ask_fill = max(pos.stop, b["open"] + inst.spread) + inst.slippage
                    close(s, ask_fill - inst.spread, t, "stop")

        # 5) Kapanış: iz süren stop, sinyaller, değerleme
        for s, b in bars.items():
            last_close[s] = b["close"]
            a, score = ind[s].at[t, "atr"], ind[s].at[t, "score"]
            if s in positions:
                pos = positions[s]
                if np.isfinite(a):
                    trail = b["close"] - pos.direction * p.stop_atr * a
                    pos.stop = max(pos.stop, trail) if pos.direction > 0 else min(pos.stop, trail)
                if score * pos.direction <= 0:
                    pending_exit.add(s)
            flat_next = s not in positions or s in pending_exit
            if flat_next and np.isfinite(a) and a > 0 and abs(score) >= p.entry_threshold - 1e-9:
                pending_entry[s] = (int(np.sign(score)), float(a))
        curve.append(balance + unrealized())

    # Dönem sonu: açık pozisyonları son kapanıştan kapat
    for s in list(positions):
        close(s, last_close[s], index[-1], "donem_sonu")
    curve[-1] = balance

    cols = ["symbol", "direction", "lots", "entry_time", "exit_time", "entry", "exit", "pnl", "costs", "r", "reason"]
    return Result(pd.Series(curve, index=pd.DatetimeIndex(index), name="equity"),
                  pd.DataFrame(trades, columns=cols), p, skipped)
