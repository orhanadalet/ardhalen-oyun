"""MetaTrader 5 bağdaştırıcısı. Yalnızca Windows'ta, MT5 terminali açıkken çalışır.

Kurulum:  pip install -r requirements.txt
DURUM: bu dosya gerçek bir terminale karşı ÇALIŞTIRILMADI; yalnızca sahte bir MetaTrader5
modülüyle istek yapısı test edildi. İlk çalıştırma mutlaka demo hesapta ve --trade olmadan olmalı.

Zaman: MT5 tüm zaman damgalarını broker SUNUCU saatinde verir. Motor ise UTC kullanır.
Çeviri üç yoldan biriyle yapılır (öncelik sırasıyla):
  1) config.server_time = {"tz": "America/New_York", "plus_hours": 7}  -> yaz saati dahil doğru
  2) config.server_utc_offset_hours = 2                                 -> sabit fark
  3) otomatik: piyasa açıkken canlı tick saatinden kestirilir
"""
from __future__ import annotations

import calendar
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .broker import Account, FatalError, OrderResult, Pos, Spec, Tick
from .config import Config

EPOCH = datetime(1970, 1, 1)


class MT5Broker:
    def __init__(self, cfg: Config, allow_real: bool = False, mt5=None, sleep=time.sleep, clock=None):
        if mt5 is None:
            import MetaTrader5 as mt5          # noqa: N813  (yalnızca Windows)
        self.mt5, self.cfg, self.allow_real, self._sleep = mt5, cfg, allow_real, sleep
        # clock: naive UTC "şimdi" döndüren fonksiyon. Canlıda sistem saati; testte/simülasyonda enjekte edilir.
        self._clock = clock or (lambda: datetime.now(timezone.utc).replace(tzinfo=None))
        if not mt5.initialize():
            raise RuntimeError(f"MT5 başlatılamadı: {mt5.last_error()}")
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("Hesap bilgisi alınamadı: MT5 terminalinde hesaba giriş yapın")
        self._login = info.login
        self._demo = info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO
        if not self._demo and not allow_real:
            raise FatalError("Bu bir DEMO hesap değil. Gerçek hesap için --allow-real gerekir.")
        for sym in cfg.symbols:
            if mt5.symbol_info(sym) is None or not mt5.symbol_select(sym, True):
                raise RuntimeError(f"Sembol bulunamadı: {sym} (Market Watch'taki tam adı yazın)")
        self._zone = None
        self._plus = timedelta(0)
        self._offset = timedelta(0)
        self._offset_at = self._wall()
        self._newest_tick = 0.0
        self._hb_at = self._wall()
        if cfg.server_time is not None:
            from .events import _zone
            self._zone = _zone(cfg.server_time["tz"])
            self._plus = timedelta(hours=float(cfg.server_time.get("plus_hours", 0)))
        elif cfg.server_utc_offset_hours is not None:
            self._offset = timedelta(hours=cfg.server_utc_offset_hours)
        else:
            first = self._newest_tick_time()
            self._sleep(3.0)
            second = self._newest_tick_time()
            if second <= first:
                raise RuntimeError("Sunucu saat farkı kestirilemedi: fiyat akmıyor (piyasa kapalı olabilir). "
                                   "Yapılandırmaya server_time ya da server_utc_offset_hours yazın.")
            self._offset = self._offset_from(second)
            self._newest_tick = second

    # ------------------------------------------------------------------ sunucu saati
    def _newest_tick_time(self) -> float:
        ticks = (self.mt5.symbol_info_tick(s) for s in self.cfg.symbols)
        return max((float(t.time) for t in ticks if t is not None), default=0.0)

    def _offset_from(self, live_tick_time: float) -> timedelta:
        """Canlı (az önce gelmiş) bir tick'in sunucu saatinden sunucu-UTC farkı. 30 dakikaya yuvarlanır."""
        diff = live_tick_time - self._wall()
        snapped = round(diff / 1800) * 1800
        if abs(diff - snapped) > 120 or not -12 * 3600 <= snapped <= 14 * 3600:
            raise RuntimeError(f"Sunucu saat farkı tutarsız ({diff:.0f} sn). "
                               "Yapılandırmaya server_time ya da server_utc_offset_hours yazın.")
        return timedelta(seconds=snapped)

    def _utc(self, server_epoch: float) -> datetime:
        if self._zone is None:
            return EPOCH + timedelta(seconds=float(server_epoch)) - self._offset
        return self._utc_index([server_epoch])[0].to_pydatetime()      # çubuklarla aynı dönüşüm

    def _utc_index(self, server_epochs) -> pd.DatetimeIndex:
        idx = pd.DatetimeIndex(pd.to_datetime(np.asarray(server_epochs, dtype="float64"), unit="s"))
        if self._zone is None:
            return idx - self._offset
        local = (idx - self._plus).tz_localize(self._zone, ambiguous=True, nonexistent="shift_forward")
        return local.tz_convert("UTC").tz_localize(None)

    def _server_epoch(self, utc: datetime) -> int:
        """UTC -> yaklaşık sunucu epoch'u (yalnızca geçmiş sorgusu aralığı için; geniş pay bırakılır)."""
        return calendar.timegm(utc.timetuple())

    # ------------------------------------------------------------------ bağlantı
    def heartbeat(self) -> None:
        """Bağlantıyı ve hesabı doğrular; koptuysa yeniden kurar; otomatik saat farkını canlı tick ile yeniler."""
        ti = self.mt5.terminal_info()
        if ti is None or not ti.connected:
            self.mt5.shutdown()
            if not self.mt5.initialize():
                raise RuntimeError(f"MT5 bağlantısı yok: {self.mt5.last_error()}")
            ti = self.mt5.terminal_info()
            if ti is None or not ti.connected:
                raise RuntimeError("MT5 terminali sunucuya bağlı değil")
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError("Hesap bilgisi alınamadı")
        if info.login != self._login:
            raise FatalError("MT5'te hesap değişti. Fitil durduruluyor; yeniden başlatın.")
        self._demo = info.trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO
        if not self._demo and not self.allow_real:
            raise FatalError("Hesap artık DEMO değil. Fitil durduruluyor.")
        if self._zone is None and self.cfg.server_utc_offset_hours is None:
            newest = self._newest_tick_time()
            wall = self._wall()
            # Canlı = bir önceki tur az önceydi VE o turdan beri yeni tick geldi. Uyku/kesinti sonrası
            # ilk turda eski bir tick "yeni" görünebilir; o tur sayılmaz.
            live = newest > self._newest_tick and wall - self._hb_at < 30
            self._hb_at = wall
            self._newest_tick = max(newest, self._newest_tick)
            if live and self._wall() - self._offset_at > 3600:
                self._offset_at = self._wall()
                try:
                    self._offset = self._offset_from(newest)
                except RuntimeError:
                    pass                                      # eski değer kalır

    def trading_enabled(self) -> bool:
        ti = self.mt5.terminal_info()
        return bool(ti is not None and ti.trade_allowed)

    def flows_since(self, since: datetime) -> list[tuple[datetime, float]]:
        """since (UTC) sonrasındaki para yatırma (+) / çekme (-) kayıtları."""
        lo = self._server_epoch(since) - 2 * 86400
        hi = int(self._wall()) + 2 * 86400
        deals = self.mt5.history_deals_get(lo, hi)
        if deals is None:
            code = self.mt5.last_error()
            if code and code[0] < 0:                         # negatif kod = hata; boş geçmiş de None dönebilir
                raise RuntimeError(f"İşlem geçmişi okunamadı: {code}")
            return []
        # Bakiye, kredi, düzeltme ve bonus kayıtları özkaynağı işlem dışı nedenle değiştirir: hepsi akıştır.
        kinds = {getattr(self.mt5, n) for n in ("DEAL_TYPE_BALANCE", "DEAL_TYPE_CREDIT", "DEAL_TYPE_CORRECTION",
                                                "DEAL_TYPE_BONUS") if hasattr(self.mt5, n)}
        out = [(self._utc(d.time), float(d.profit)) for d in deals if d.type in kinds]
        return [(t, a) for t, a in out if t > since]

    # ------------------------------------------------------------------ Broker arayüzü
    def now(self) -> datetime:
        return self._clock()

    def _wall(self) -> float:
        """Şimdiki zamanın UTC epoch'u (saniye)."""
        return (self._clock() - EPOCH).total_seconds()

    def account(self) -> Account:
        a = self.mt5.account_info()
        if a is None:
            raise RuntimeError("Hesap bilgisi alınamadı")
        return Account(a.equity, a.balance, a.margin_free, a.currency, self._demo)

    def spec(self, symbol: str) -> Spec:
        s = self.mt5.symbol_info(symbol)
        if s is None:
            raise RuntimeError(f"Sembol bilgisi alınamadı: {symbol}")
        return Spec(symbol, s.digits, s.point, s.volume_min, s.volume_step, s.volume_max,
                    s.trade_stops_level * s.point, s.trade_mode == self.mt5.SYMBOL_TRADE_MODE_FULL)

    def tick(self, symbol: str) -> Tick | None:
        t = self.mt5.symbol_info_tick(symbol)
        if t is None or t.bid <= 0 or t.ask <= 0:
            return None
        return Tick(t.bid, t.ask, self._utc(t.time))

    def _rates(self, symbol: str, count: int):
        minutes = self.cfg.strategy.bar_minutes
        tf = self.mt5.TIMEFRAME_H1 if minutes == 60 else getattr(self.mt5, f"TIMEFRAME_M{minutes}")
        return self.mt5.copy_rates_from_pos(symbol, tf, 1, count)   # 1: oluşan çubuğu atla

    def last_bar_time(self, symbol: str) -> datetime | None:
        r = self._rates(symbol, 1)
        return None if r is None or len(r) == 0 else self._utc(float(r[-1]["time"]))

    def bars(self, symbol: str, count: int) -> pd.DataFrame:
        r = self._rates(symbol, count)
        if r is None or len(r) == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close"])
        df = pd.DataFrame(r)[["time", "open", "high", "low", "close"]]
        df.index = self._utc_index(df.pop("time"))
        df.index.name = "time"
        return df[~df.index.duplicated(keep="last")]

    def positions(self) -> list[Pos]:
        raw = self.mt5.positions_get()
        if raw is None:
            # Hata "pozisyon yok" sayılmaz: sayılırsa sınırlar sıfır risk görür ve ikinci pozisyon açılabilir.
            raise RuntimeError(f"Pozisyonlar okunamadı: {self.mt5.last_error()}")
        out = []
        for p in raw:
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
        if profit is None:
            raise RuntimeError(f"Risk hesaplanamadı: {symbol} {self.mt5.last_error()}")
        return max(0.0, -profit)

    def margin_ok(self, symbol: str, direction: int, lots: float) -> bool:
        t = self.mt5.symbol_info_tick(symbol)
        a = self.mt5.account_info()
        if t is None or a is None:
            return False
        need = self.mt5.order_calc_margin(self._order_type(direction), symbol, lots, t.ask if direction > 0 else t.bid)
        return need is not None and need <= a.margin_free

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
        ok = res.retcode in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_DONE_PARTIAL,
                             self.mt5.TRADE_RETCODE_PLACED)
        return OrderResult(ok, getattr(res, "order", 0), getattr(res, "price", 0.0), f"{res.retcode} {res.comment}")

    def market_order(self, symbol: str, direction: int, lots: float, sl: float, comment: str) -> OrderResult:
        if sl <= 0:
            return OrderResult(False, message="stop-loss olmadan emir gönderilmez")
        t = self.mt5.symbol_info_tick(symbol)
        if t is None:
            return OrderResult(False, message="fiyat yok")
        res = self._send({
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": lots,
            "type": self._order_type(direction), "price": t.ask if direction > 0 else t.bid,
            "sl": sl, "deviation": self.cfg.deviation_points, "magic": self.cfg.magic, "comment": comment,
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(symbol),
        })
        if not res.ok:
            return res
        for _ in range(5):                   # pozisyon listesi emirden biraz geç güncellenebilir
            mine = [p for p in self.positions() if p.symbol == symbol]
            if mine:
                p = max(mine, key=lambda x: x.open_time)
                return OrderResult(True, p.ticket, p.entry, res.message)
            self._sleep(0.3)
        return OrderResult(False, message=f"emir kabul edildi ama pozisyon henüz görünmüyor ({res.message})")

    def modify_sl(self, pos: Pos, sl: float) -> bool:
        return self._send({"action": self.mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol,
                           "position": pos.ticket, "sl": sl, "tp": 0.0, "magic": self.cfg.magic}).ok

    def close(self, pos: Pos) -> OrderResult:
        t = self.mt5.symbol_info_tick(pos.symbol)
        if t is None:
            return OrderResult(False, message="fiyat yok")
        return self._send({
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.lots,
            "type": self._order_type(-pos.direction), "position": pos.ticket,
            "price": t.bid if pos.direction > 0 else t.ask, "deviation": self.cfg.deviation_points,
            "magic": self.cfg.magic, "comment": "fitil-kapat", "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(pos.symbol),
        })

    def shutdown(self) -> None:
        self.mt5.shutdown()
