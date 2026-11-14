"""Broker arayüzü ve test/simülasyon için PaperBroker."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd

BAR = timedelta(minutes=15)


@dataclass(frozen=True)
class Spec:
    name: str
    digits: int
    point: float
    volume_min: float
    volume_step: float
    volume_max: float
    stops_level: float = 0.0     # fiyat biriminde en az stop mesafesi
    tradable: bool = True


@dataclass(frozen=True)
class Tick:
    bid: float
    ask: float
    time: datetime               # UTC


@dataclass(frozen=True)
class Account:
    equity: float
    balance: float
    margin_free: float
    currency: str
    is_demo: bool


@dataclass
class Pos:
    ticket: int
    symbol: str
    direction: int
    lots: float
    entry: float
    sl: float
    open_time: datetime


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    ticket: int = 0
    price: float = 0.0
    message: str = ""


class FatalError(RuntimeError):
    """Botun durması gereken durum (ör. hesap değişti, gerçek hesaba geçildi)."""


class Broker(Protocol):
    def now(self) -> datetime: ...
    def heartbeat(self) -> None: ...                                    # bağlantıyı doğrula / yeniden kur
    def flows_since(self, since: datetime) -> list[tuple[datetime, float]]: ...   # (UTC zaman, tutar): yatırma +, çekme -
    def account(self) -> Account: ...
    def spec(self, symbol: str) -> Spec: ...
    def tick(self, symbol: str) -> Tick | None: ...
    def last_bar_time(self, symbol: str) -> datetime | None: ...       # son KAPANMIŞ çubuğun açılış zamanı (UTC)
    def bars(self, symbol: str, count: int) -> pd.DataFrame: ...        # kapanmış M15 çubukları, UTC indeks
    def positions(self) -> list[Pos]: ...                               # yalnızca bu botun pozisyonları
    def loss_per_lot(self, symbol: str, direction: int, entry: float, sl: float) -> float: ...
    def margin_ok(self, symbol: str, direction: int, lots: float) -> bool: ...
    def market_order(self, symbol: str, direction: int, lots: float, sl: float, comment: str) -> OrderResult: ...
    def modify_sl(self, pos: Pos, sl: float) -> bool: ...
    def close(self, pos: Pos) -> OrderResult: ...


def round_lots_down(spec: Spec, lots: float) -> float:
    """Adıma AŞAĞI yuvarlar; minimumun altındaysa 0 (risk asla büyütülmez)."""
    if not math.isfinite(lots) or lots <= 0:
        return 0.0
    v = math.floor(lots / spec.volume_step + 1e-9) * spec.volume_step
    if v < spec.volume_min - 1e-12:
        return 0.0
    return round(min(v, spec.volume_max), 8)


class PaperBroker:
    """Geçmiş M15 çubukları üzerinde çalışan simüle broker (test ve backtest için).

    Saat T'de: T'den önce açılmış çubuklar kapanmıştır; tick = son kapanış (bid), ask = bid+spread.
    Piyasa emri o anki tick'ten dolar. advance() bir çubuk ilerletir ve yeni çubukta stopları işler;
    açılış stopu boşlukla geçtiyse açılıştan (kötü fiyattan) dolar.
    """

    def __init__(self, data: dict[str, pd.DataFrame], specs: dict[str, Spec], contract: dict[str, float],
                 usd_side: dict[str, str], spread: dict[str, float], equity: float = 10_000.0,
                 commission_per_lot: float = 7.0, slippage: dict[str, float] | None = None,
                 start: datetime | None = None):
        self.data, self.specs, self.contract, self.usd_side, self.spread = data, specs, contract, usd_side, spread
        self.commission, self.slippage = commission_per_lot, slippage or {}
        self.balance = equity
        self._pos: dict[int, Pos] = {}
        self._next = 1
        self.closed: list[dict] = []
        self._flows: list[tuple[datetime, float]] = []
        self._meta: dict[int, tuple[float, str]] = {}        # ticket -> (başlangıç riski, emir açıklaması)
        self.timeline = sorted(set().union(*[df.index for df in data.values()]))
        self._i = 0
        if start is not None:
            self._i = next(i for i, t in enumerate(self.timeline) if t >= pd.Timestamp(start))
        self._last: dict[str, float] = {}
        for s, df in data.items():
            prior = df.loc[df.index < self.clock]
            if len(prior):
                self._last[s] = float(prior["close"].iloc[-1])

    # --- zaman ---
    @property
    def clock(self) -> pd.Timestamp:
        return self.timeline[self._i]

    def now(self) -> datetime:
        return self.clock.to_pydatetime()

    def advance(self) -> bool:
        """Saati bir çubuk ilerletir. Veri bittiyse False."""
        t = self.clock
        for s, df in self.data.items():
            if t not in df.index:
                continue
            b = df.loc[t]
            for p in [p for p in self._pos.values() if p.symbol == s]:
                slip = self.slippage.get(s, 0.0)
                if p.direction > 0 and b["low"] <= p.sl:
                    self._settle(p, min(p.sl, b["open"]) - slip, t + BAR, "stop")
                elif p.direction < 0 and b["high"] + self.spread[s] >= p.sl:
                    self._settle(p, max(p.sl, b["open"] + self.spread[s]) + slip, t + BAR, "stop")
            self._last[s] = float(b["close"])
        if self._i + 1 >= len(self.timeline):
            return False
        self._i += 1
        return True

    # --- Broker arayüzü ---
    def _usd(self, symbol: str, price: float) -> float:
        return self.contract[symbol] if self.usd_side[symbol] == "quote" else self.contract[symbol] / price

    def _pnl(self, p: Pos, exit_price: float) -> float:
        return p.direction * p.lots * (exit_price - p.entry) * self._usd(p.symbol, exit_price)

    def _exit_price(self, p: Pos) -> float:
        bid = self._last[p.symbol]
        return bid if p.direction > 0 else bid + self.spread[p.symbol]

    def heartbeat(self) -> None:
        return None

    def flows_since(self, since: datetime) -> list[tuple[datetime, float]]:
        return [(t, a) for t, a in self._flows if t > since]

    def deposit(self, amount: float) -> None:
        """Test için: para yatırma (+) ya da çekme (-)."""
        self.balance += amount
        self._flows.append((self.now(), amount))

    def account(self) -> Account:
        eq = self.balance + sum(self._pnl(p, self._exit_price(p)) for p in self._pos.values())
        return Account(eq, self.balance, eq, "USD", True)

    def spec(self, symbol: str) -> Spec:
        return self.specs[symbol]

    def tick(self, symbol: str) -> Tick | None:
        if symbol not in self._last:
            return None
        bid = self._last[symbol]
        return Tick(bid, bid + self.spread[symbol], self.now())

    def last_bar_time(self, symbol: str) -> datetime | None:
        idx = self.data[symbol].index
        k = idx.searchsorted(self.clock, side="left")
        return idx[k - 1].to_pydatetime() if k > 0 else None

    def bars(self, symbol: str, count: int) -> pd.DataFrame:
        df = self.data[symbol]
        k = df.index.searchsorted(self.clock, side="left")
        return df.iloc[max(0, k - count):k]

    def positions(self) -> list[Pos]:
        return list(self._pos.values())

    def loss_per_lot(self, symbol: str, direction: int, entry: float, sl: float) -> float:
        return max(0.0, direction * (entry - sl)) * self._usd(symbol, sl)

    def margin_ok(self, symbol: str, direction: int, lots: float) -> bool:
        return True

    def market_order(self, symbol: str, direction: int, lots: float, sl: float, comment: str) -> OrderResult:
        t = self.tick(symbol)
        if t is None:
            return OrderResult(False, message="fiyat yok")
        price = (t.ask if direction > 0 else t.bid) + direction * self.slippage.get(symbol, 0.0)
        if (price - sl) * direction <= 0:
            return OrderResult(False, message="stop yanlış tarafta")
        self.balance -= self.commission * lots
        p = Pos(self._next, symbol, direction, lots, price, sl, self.now())
        self._pos[p.ticket] = p
        self._meta[p.ticket] = (self.loss_per_lot(symbol, direction, price, sl) * lots, comment)
        self._next += 1
        return OrderResult(True, p.ticket, price)

    def modify_sl(self, pos: Pos, sl: float) -> bool:
        if pos.ticket not in self._pos:
            return False
        self._pos[pos.ticket].sl = sl
        return True

    def close(self, pos: Pos) -> OrderResult:
        if pos.ticket not in self._pos:
            return OrderResult(False, message="pozisyon yok")
        p = self._pos[pos.ticket]
        price = self._exit_price(p) - p.direction * self.slippage.get(p.symbol, 0.0)
        self._settle(p, price, self.clock, "kapat")
        return OrderResult(True, p.ticket, price)

    def _settle(self, p: Pos, price: float, when, reason: str) -> None:
        pnl = self._pnl(p, price)
        self.balance += pnl
        risk, comment = self._meta.pop(p.ticket, (0.0, ""))
        net = pnl - self.commission * p.lots
        self.closed.append(dict(ticket=p.ticket, symbol=p.symbol, direction=p.direction, lots=p.lots,
                                entry=p.entry, exit=price, open_time=p.open_time, close_time=when,
                                pnl=net, risk=risk, r=net / risk if risk > 0 else float("nan"),
                                setup=comment.replace("fitil-", ""), reason=reason))
        del self._pos[p.ticket]
