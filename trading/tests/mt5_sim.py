"""MetaTrader5 Python arayüzünün simülatörü (yalnızca test için).

Gerçek paketin fonksiyon adlarını, alan adlarını, sabit değerlerini (paket 5.0.6231 ile karşılaştırıldı)
ve davranışını taklit eder: tüm zaman damgaları SUNUCU saatindedir (New York + 7 saat, yaz saati dahil),
emir bileti pozisyon biletinden farklıdır, okuma hataları None döner. Arkasında bir PaperBroker çalışır.
Amaç: canlı kod yolunu (MT5Broker + Engine) baştan sona çalıştırıp sonucu doğrudan simülasyonla karşılaştırmak.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo

import numpy as np

EPOCH = datetime(1970, 1, 1)


def pd_ts(x):
    return x.to_pydatetime() if hasattr(x, "to_pydatetime") else x


class SimMT5:
    ACCOUNT_TRADE_MODE_DEMO, ACCOUNT_TRADE_MODE_REAL = 0, 2
    SYMBOL_TRADE_MODE_FULL = 4
    TIMEFRAME_M5, TIMEFRAME_M15, TIMEFRAME_M30, TIMEFRAME_H1 = 5, 15, 30, 1 | 0x4000
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_PLACED, TRADE_RETCODE_DONE, TRADE_RETCODE_DONE_PARTIAL = 10008, 10009, 10010
    TRADE_RETCODE_INVALID_STOPS, TRADE_RETCODE_REJECT = 10016, 10006
    DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_ENTRY_INOUT, DEAL_ENTRY_OUT_BY = 0, 1, 2, 3
    DEAL_REASON_CLIENT, DEAL_REASON_EXPERT, DEAL_REASON_SL, DEAL_REASON_SO = 0, 3, 4, 6
    DEAL_TYPE_BALANCE, DEAL_TYPE_CREDIT, DEAL_TYPE_CHARGE, DEAL_TYPE_CORRECTION, DEAL_TYPE_BONUS = 2, 3, 4, 5, 6
    TRADE_RETCODE_REQUOTE = 10004

    def __init__(self, paper, zone: str = "America/New_York", plus_hours: float = 7, magic_of_others: int = 0,
                 demo: bool = True, login: int = 424242, expected_timeframe: int | None = None,
                 position_lag_calls: int = 0, stops_level_points: int = 0, filling_mode: int = 1,
                 deal_lag_calls: int = 0, max_deviation_check: bool = True):
        self.paper, self.zone, self.plus = paper, ZoneInfo(zone), timedelta(hours=plus_hours)
        self.demo, self.login, self.connected = demo, login, True
        self.magic: dict[int, int] = {}            # pozisyon bileti -> magic
        self.requests: list[dict] = []
        self.fail_positions = False
        self.expected_timeframe = expected_timeframe
        self.position_lag_calls = position_lag_calls      # emirden sonra pozisyon listesi bu kadar çağrı gecikir
        self._hidden: dict[int, int] = {}
        self.stops_level_points = stops_level_points
        self.filling_mode = filling_mode                  # 1 = FOK, 2 = IOC, 3 = ikisi, 0 = yalnız RETURN
        self.deal_lag_calls = deal_lag_calls              # bakiye kaydı geçmişte bu kadar çağrı geç görünür
        self._deal_calls: dict[tuple, int] = {}
        self.max_deviation_check = max_deviation_check
        self.wall_extra = 0.0                             # canlı piyasa: beklerken tick saati ilerler
        self.price_drift = 0.0                            # emir anında fiyat kayması (requote sınaması)
        self.api_closed: set[int] = set()                 # Python API ile kapatılan pozisyonlar (neden: EXPERT)
        self._err = (1, "Success")

    # --- zaman: UTC -> sunucu epoch'u ---
    def srv(self, utc: datetime) -> int:
        local = utc.replace(tzinfo=timezone.utc).astimezone(self.zone).replace(tzinfo=None)
        return int((local + self.plus - EPOCH).total_seconds())

    # --- oturum ---
    def initialize(self):
        self.connected = True
        return True

    def shutdown(self):
        return None

    def last_error(self):
        return self._err

    def terminal_info(self):
        return NS(connected=self.connected, trade_allowed=True)

    def account_info(self):
        a = self.paper.account()
        return NS(login=self.login, trade_mode=0 if self.demo else 2, equity=a.equity, balance=a.balance,
                  margin_free=a.margin_free, currency="USD")

    # --- semboller ve fiyat ---
    def symbol_select(self, symbol, enable=True):
        return symbol in self.paper.specs

    def symbol_info(self, symbol):
        if symbol not in self.paper.specs:
            return None
        s = self.paper.specs[symbol]
        return NS(digits=s.digits, point=s.point, volume_min=s.volume_min, volume_step=s.volume_step,
                  volume_max=s.volume_max, trade_stops_level=self.stops_level_points, trade_mode=4,
                  filling_mode=self.filling_mode,
                  trade_contract_size=self.paper.contract[symbol])

    def symbol_info_tick(self, symbol):
        t = self.paper.tick(symbol)
        if t is None:
            return None
        return NS(bid=t.bid, ask=t.ask, time=self.srv(t.time + timedelta(seconds=self.wall_extra)))

    def advance_wall(self, seconds: float) -> None:
        """sleep yerine kullanılır: gerçek zaman ilerler, yeni tick gelir."""
        self.wall_extra += seconds

    def wall_now(self):
        return self.paper.now() + timedelta(seconds=self.wall_extra)

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        if self.expected_timeframe is not None and timeframe != self.expected_timeframe:
            raise AssertionError(f"beklenmeyen zaman dilimi sabiti: {timeframe}")
        if start_pos < 1:
            raise AssertionError("bot oluşan çubuğu (pozisyon 0) okumamalı")
        skip = start_pos - 1                       # pozisyon 1 = son kapanmış çubuk; daha büyükse daha eskiye gidilir
        df = self.paper.bars(symbol, count + skip)
        df = df.iloc[:len(df) - skip] if skip else df
        arr = np.zeros(len(df), dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                                       ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
        arr["time"] = [self.srv(t.to_pydatetime()) for t in df.index]
        for k in ("open", "high", "low", "close"):
            arr[k] = df[k].to_numpy()
        arr["spread"] = int(round(self.paper.spread[symbol] / self.paper.specs[symbol].point))
        arr["tick_volume"] = 100
        return arr

    # --- pozisyonlar ---
    def positions_get(self):
        if self.fail_positions:
            self._err = (-10004, "No IPC connection")
            return None
        out = []
        for p in self.paper.positions():
            if self._hidden.get(p.ticket, 0) > 0:          # emir yeni işlendi: liste henüz güncellenmedi
                self._hidden[p.ticket] -= 1
                continue
            out.append(NS(ticket=p.ticket, symbol=p.symbol, type=0 if p.direction > 0 else 1, volume=p.lots,
                          price_open=p.entry, sl=p.sl, tp=0.0, time=self.srv(p.open_time),
                          magic=self.magic.get(p.ticket, 0)))
        return tuple(out)

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        direction = 1 if order_type == self.ORDER_TYPE_BUY else -1
        return direction * volume * (price_close - price_open) * self.paper._usd(symbol, price_close)

    def order_calc_margin(self, order_type, symbol, volume, price):
        return volume * self.paper._usd(symbol, price) * price / 100.0          # 1:100 kaldıraç

    def order_send(self, request):
        self.requests.append(dict(request))
        action, symbol = request["action"], request["symbol"]
        if action == self.TRADE_ACTION_SLTP:
            pos = next((p for p in self.paper.positions() if p.ticket == request["position"]), None)
            ok = pos is not None and self.paper.modify_sl(pos, request["sl"])
            return NS(retcode=self.TRADE_RETCODE_DONE if ok else self.TRADE_RETCODE_INVALID_STOPS, order=0, price=0.0,
                      comment="done" if ok else "invalid stops")
        if action != self.TRADE_ACTION_DEAL:
            return NS(retcode=self.TRADE_RETCODE_REJECT, order=0, price=0.0, comment="unsupported")
        for key in ("volume", "type", "price", "deviation", "magic", "type_time", "type_filling"):
            if key not in request:
                raise AssertionError(f"istekte eksik alan: {key}")
        allowed = {self.ORDER_FILLING_RETURN}
        if self.filling_mode & 1:
            allowed.add(self.ORDER_FILLING_FOK)
        if self.filling_mode & 2:
            allowed.add(self.ORDER_FILLING_IOC)
        if request["type_filling"] not in allowed or (self.filling_mode and request["type_filling"] == self.ORDER_FILLING_RETURN):
            return NS(retcode=10030, order=0, price=0.0, comment="unsupported filling mode")
        direction = 1 if request["type"] == self.ORDER_TYPE_BUY else -1
        tick = self.paper.tick(symbol)
        spec = self.paper.specs[symbol]
        market = (tick.ask if direction > 0 else tick.bid) + self.price_drift
        if self.max_deviation_check and abs(request["price"] - market) > request["deviation"] * spec.point + 1e-12:
            return NS(retcode=10004, order=0, price=0.0, comment="requote")
        if "position" not in request and self.stops_level_points:
            if (market - request.get("sl", 0.0)) * direction < self.stops_level_points * spec.point - 1e-12:
                return NS(retcode=self.TRADE_RETCODE_INVALID_STOPS, order=0, price=0.0, comment="invalid stops")
        if "position" in request:                                                # kapatma: ters yönde işlem
            pos = next((p for p in self.paper.positions() if p.ticket == request["position"]), None)
            if pos is None or direction != -pos.direction or abs(request["volume"] - pos.lots) > 1e-9:
                return NS(retcode=self.TRADE_RETCODE_REJECT, order=0, price=0.0, comment="bad close request")
            res = self.paper.close(pos)
            self.api_closed.add(pos.ticket)
            return NS(retcode=self.TRADE_RETCODE_DONE, order=900_000 + pos.ticket, price=res.price, comment="done")
        res = self.paper.market_order(symbol, direction, request["volume"], request.get("sl", 0.0), request.get("comment", ""))
        if not res.ok:
            return NS(retcode=self.TRADE_RETCODE_INVALID_STOPS, order=0, price=0.0, comment=res.message)
        self.magic[res.ticket] = request["magic"]
        if self.position_lag_calls:
            self._hidden[res.ticket] = self.position_lag_calls
        return NS(retcode=self.TRADE_RETCODE_DONE, order=500_000 + res.ticket, price=res.price, comment="done")

    def history_deals_get(self, date_from=None, date_to=None, position=None):
        if position is not None:                          # bir pozisyonun giriş ve çıkış işlemleri
            t = next((x for x in self.paper.closed if x["ticket"] == position), None)
            if t is None:
                return ()
            commission = self.paper.commission * t["lots"]
            gross = t["pnl"] + commission
            reason = (self.DEAL_REASON_SL if t["reason"] == "stop" else
                      self.DEAL_REASON_EXPERT if position in self.api_closed else self.DEAL_REASON_CLIENT)
            return (NS(entry=self.DEAL_ENTRY_IN, profit=0.0, commission=-commission, swap=0.0, reason=self.DEAL_REASON_EXPERT,
                       time=self.srv(t["open_time"]), position_id=position),
                    NS(entry=self.DEAL_ENTRY_OUT, profit=gross, commission=0.0, swap=0.0, reason=reason,
                       time=self.srv(pd_ts(t["close_time"])), position_id=position))
        out = []
        for t, a in self.paper._flows:
            n = self._deal_calls.get((t, a), 0)
            self._deal_calls[(t, a)] = n + 1
            if n < self.deal_lag_calls:                    # özkaynak değişti ama kayıt geçmişte henüz yok
                continue
            out.append(NS(type=self.DEAL_TYPE_BALANCE, profit=a, time=self.srv(t)))
        return tuple(d for d in out if date_from <= d.time <= date_to)
