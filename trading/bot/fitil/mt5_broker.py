"""MetaTrader 5 bağdaştırıcısı. Yalnızca Windows'ta, MT5 terminali açıkken çalışır.

Kurulum:  pip install MetaTrader5 pandas numpy
DURUM: bu dosya gerçek bir terminale karşı ÇALIŞTIRILMADI; yalnızca sahte bir MetaTrader5
modülüyle istek yapısı test edildi. İlk çalıştırma mutlaka demo hesapta ve --dry-run ile olmalı.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from .broker import Account, OrderResult, Pos, Spec, Tick
from .config import Config


class MT5Broker:
    def __init__(self, cfg: Config, allow_real: bool = False, mt5=None):
        if mt5 is None:
            import MetaTrader5 as mt5          # noqa: N813  (yalnızca Windows)
        self.mt5, self.cfg = mt5, cfg
        if not mt5.initialize():
            raise RuntimeError(f"MT5 başlatılamadı: {mt5.last_error()}")
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("Hesap bilgisi alınamadı: MT5 terminalinde hesaba giriş yapın")
        self._demo = info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        if not self._demo and not allow_real:
            raise RuntimeError("Bu bir DEMO hesap değil. Gerçek hesap için --allow-real gerekir.")
        for sym in cfg.symbols:
            if mt5.symbol_info(sym) is None or not mt5.symbol_select(sym, True):
                raise RuntimeError(f"Sembol bulunamadı: {sym} (Market Watch'taki tam adı yazın)")
        self._offset = self._server_offset()

    # --- sunucu saati ---
    def _server_offset(self) -> timedelta:
        """Sunucu saati - UTC. MT5 zaman damgaları sunucu saatindedir."""
        if self.cfg.server_utc_offset_hours is not None:
            return timedelta(hours=self.cfg.server_utc_offset_hours)
        newest = max((t.time for t in (self.mt5.symbol_info_tick(s) for s in self.cfg.symbols) if t), default=0)
        diff = newest - time.time()
        if newest == 0 or abs(diff - round(diff / 1800) * 1800) > 300:
            raise RuntimeError("Sunucu saat farkı kestirilemedi (piyasa kapalı olabilir). "
                               "Yapılandırmaya server_utc_offset_hours yazın.")
        return timedelta(seconds=round(diff / 1800) * 1800)

    def _utc(self, server_epoch: float) -> datetime:
        return datetime.fromtimestamp(server_epoch, timezone.utc).replace(tzinfo=None) - self._offset

    # --- Broker arayüzü ---
    def now(self) -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    def account(self) -> Account:
        a = self.mt5.account_info()
        return Account(a.equity, a.balance, a.margin_free, a.currency, self._demo)

    def spec(self, symbol: str) -> Spec:
        s = self.mt5.symbol_info(symbol)
        return Spec(symbol, s.digits, s.point, s.volume_min, s.volume_step, s.volume_max,
                    s.trade_stops_level * s.point, s.trade_mode == self.mt5.SYMBOL_TRADE_MODE_FULL)

    def tick(self, symbol: str) -> Tick | None:
        t = self.mt5.symbol_info_tick(symbol)
        if t is None or t.bid <= 0 or t.ask <= 0:
            return None
        return Tick(t.bid, t.ask, self._utc(t.time))

    def _rates(self, symbol: str, count: int):
        return self.mt5.copy_rates_from_pos(symbol, self.mt5.TIMEFRAME_M15, 1, count)   # 1: oluşan çubuğu atla

    def last_bar_time(self, symbol: str) -> datetime | None:
        r = self._rates(symbol, 1)
        return None if r is None or len(r) == 0 else self._utc(float(r[-1]["time"]))

    def bars(self, symbol: str, count: int) -> pd.DataFrame:
        r = self._rates(symbol, count)
        if r is None or len(r) == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close"])
        df = pd.DataFrame(r)[["time", "open", "high", "low", "close"]]
        df.index = pd.to_datetime(df.pop("time"), unit="s") - self._offset
        df.index.name = "time"
        return df

    def positions(self) -> list[Pos]:
        out = []
        for p in self.mt5.positions_get() or ():
            if p.magic != self.cfg.magic:
                continue
            direction = 1 if p.type == self.mt5.POSITION_TYPE_BUY else -1
            out.append(Pos(p.ticket, p.symbol, direction, p.volume, p.price_open, p.sl, self._utc(p.time)))
        return out

    def _order_type(self, direction: int):
        return self.mt5.ORDER_TYPE_BUY if direction > 0 else self.mt5.ORDER_TYPE_SELL

    def loss_per_lot(self, symbol: str, direction: int, entry: float, sl: float) -> float:
        if sl <= 0:
            return float("inf")              # stopsuz pozisyon: sınırsız risk say
        profit = self.mt5.order_calc_profit(self._order_type(direction), symbol, 1.0, entry, sl)
        return 0.0 if profit is None else max(0.0, -profit)

    def margin_ok(self, symbol: str, direction: int, lots: float) -> bool:
        t = self.mt5.symbol_info_tick(symbol)
        price = t.ask if direction > 0 else t.bid
        need = self.mt5.order_calc_margin(self._order_type(direction), symbol, lots, price)
        return need is not None and need <= self.mt5.account_info().margin_free

    def _filling(self, symbol: str):
        mode = self.mt5.symbol_info(symbol).filling_mode
        if mode & 1:
            return self.mt5.ORDER_FILLING_FOK
        if mode & 2:
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _send(self, request: dict) -> OrderResult:
        res = self.mt5.order_send(request)
        if res is None:
            return OrderResult(False, message=str(self.mt5.last_error()))
        ok = res.retcode == self.mt5.TRADE_RETCODE_DONE
        return OrderResult(ok, getattr(res, "order", 0), getattr(res, "price", 0.0), f"{res.retcode} {res.comment}")

    def market_order(self, symbol: str, direction: int, lots: float, sl: float, comment: str) -> OrderResult:
        if sl <= 0:
            return OrderResult(False, message="stop-loss olmadan emir gönderilmez")
        t = self.mt5.symbol_info_tick(symbol)
        res = self._send({
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lots,
            "type": self._order_type(direction), "price": t.ask if direction > 0 else t.bid,
            "sl": sl, "deviation": self.cfg.deviation_points, "magic": self.cfg.magic, "comment": comment,
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(symbol),
        })
        if not res.ok:
            return res
        mine = [p for p in self.positions() if p.symbol == symbol]
        if not mine:
            return OrderResult(False, message="emir kabul edildi ama pozisyon bulunamadı")
        p = max(mine, key=lambda x: x.open_time)
        return OrderResult(True, p.ticket, p.entry, res.message)

    def modify_sl(self, pos: Pos, sl: float) -> bool:
        return self._send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol,
                           "position": pos.ticket, "sl": sl, "tp": 0.0, "magic": self.cfg.magic}).ok

    def close(self, pos: Pos) -> OrderResult:
        t = self.mt5.symbol_info_tick(pos.symbol)
        return self._send({
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.lots,
            "type": self._order_type(-pos.direction), "position": pos.ticket,
            "price": t.bid if pos.direction > 0 else t.ask, "deviation": self.cfg.deviation_points,
            "magic": self.cfg.magic, "comment": "fitil-kapat", "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(pos.symbol),
        })

    def shutdown(self) -> None:
        self.mt5.shutdown()
