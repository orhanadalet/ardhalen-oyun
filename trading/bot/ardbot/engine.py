"""Motor: yeni M15 çubuğunu yakalar, sinyali değerlendirir, riski boyutlandırır, pozisyonu yönetir.

step() tek bir tur atar; run() bunu canlıda scan_interval_sec aralıkla tekrarlar.
Koruma brokerdadır: her emir stop-loss ile gönderilir, bot dursa da stop yerinde kalır.
"""
from __future__ import annotations

import csv
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .broker import BAR, Broker, Pos, round_lots_down
from .config import Config
from .strategy import Signal, atr, evaluate


class Engine:
    def __init__(self, broker: Broker, cfg: Config, state_path: str | Path | None = None,
                 journal_path: str | Path | None = None, dry_run: bool = False, log=print):
        self.broker, self.cfg, self.dry_run, self.log = broker, cfg, dry_run, log
        self.state_path = Path(state_path) if state_path else None
        self.journal_path = Path(journal_path) if journal_path else None
        self.seen: dict[str, datetime] = {}
        self.state = {"day": "", "day_start_equity": 0.0, "peak_equity": 0.0, "trades_today": 0, "tickets": {}}
        if self.state_path and self.state_path.exists():
            self.state.update(json.loads(self.state_path.read_text(encoding="utf-8")))
        self._blackouts = [datetime.fromisoformat(x) for x in cfg.blackouts]

    # ------------------------------------------------------------------ durum
    def _save(self) -> None:
        if self.state_path:
            self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def _journal(self, now: datetime, event: str, symbol: str = "", **kw) -> None:
        self.log(f"{now:%Y-%m-%d %H:%M:%S} {event:10s} {symbol:10s} " + " ".join(f"{k}={v}" for k, v in kw.items()))
        if not self.journal_path:
            return
        new = not self.journal_path.exists()
        with self.journal_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time_utc", "event", "symbol", "detail"])
            w.writerow([now.isoformat(), event, symbol, json.dumps(kw, ensure_ascii=False, default=str)])

    def _roll_day(self, now: datetime, equity: float) -> None:
        day = now.strftime("%Y-%m-%d")
        changed = False
        if self.state["day"] != day:
            self.state.update(day=day, day_start_equity=equity, trades_today=0)
            changed = True
        if equity > self.state["peak_equity"]:
            self.state["peak_equity"] = equity
            changed = True
        if changed:
            self._save()

    # ------------------------------------------------------------------ kapılar
    def drawdown(self, equity: float) -> float:
        peak = self.state["peak_equity"]
        return 0.0 if peak <= 0 else max(0.0, 1.0 - equity / peak)

    def risk_factor(self, equity: float) -> float:
        """Zirveden düşüşe göre risk çarpanı: 1.0, 0.5 ya da 0 (dur)."""
        dd = self.drawdown(equity)
        if dd >= self.cfg.dd_halt:
            return 0.0
        return 0.5 if dd >= self.cfg.dd_half_risk else 1.0

    def entry_block(self, now: datetime, equity: float, n_positions: int) -> str | None:
        """Yeni giriş yasaksa nedenini döndürür."""
        c = self.cfg
        if now.weekday() >= 5:
            return "hafta sonu"
        if not c.entry_hours[0] <= now.hour < c.entry_hours[1]:
            return "seans dışı"
        start = self.state["day_start_equity"]
        if start > 0 and (start - equity) / start >= c.daily_loss_limit:
            return "günlük kayıp sınırı"
        if self.risk_factor(equity) == 0.0:
            return "düşüş freni: sistem durdu"
        if self.state["trades_today"] >= c.max_trades_per_day:
            return "günlük işlem sınırı"
        if n_positions >= c.max_positions:
            return "azami pozisyon sayısı"
        for b in self._blackouts:
            if abs((now - b).total_seconds()) <= c.blackout_minutes * 60:
                return "veri saati"
        return None

    def _open_risk(self, positions: list[Pos]) -> tuple[float, float]:
        total = usd = 0.0
        for p in positions:
            r = self.broker.loss_per_lot(p.symbol, p.direction, p.entry, p.sl) * p.lots
            total += r
            usd += r * p.direction * self.cfg.symbols.get(p.symbol, {}).get("usd_beta", 0)
        return total, usd

    # ------------------------------------------------------------------ tur
    def step(self) -> None:
        b, c = self.broker, self.cfg
        now = b.now()
        acct = b.account()
        self._roll_day(now, acct.equity)
        positions = b.positions()

        live = {str(p.ticket) for p in positions}
        stale = [t for t in self.state["tickets"] if t not in live]
        if stale:
            for t in stale:
                del self.state["tickets"][t]
            self._save()

        if now.hour >= c.flat_hour or now.weekday() >= 5:
            for p in positions:
                self._close(now, p, "gün sonu")
            return

        for sym in c.symbols:
            last = b.last_bar_time(sym)
            if last is None or self.seen.get(sym) == last:
                continue
            closed_at = last + BAR
            if (now - closed_at).total_seconds() < c.settle_sec:
                continue
            self.seen[sym] = last
            df = b.bars(sym, c.history_bars)
            if len(df) == 0:
                continue
            mine = [p for p in positions if p.symbol == sym]
            if mine:
                for p in mine:
                    self._manage(now, p, df)
                continue
            if (now - closed_at).total_seconds() > c.max_signal_age_sec:
                continue                     # bot geç başladı: eski çubuğa girme
            block = self.entry_block(now, acct.equity, len(positions))
            if block:
                continue
            tick = b.tick(sym)
            if tick is None:
                continue
            sig = evaluate(df, c.strategy, tick.ask - tick.bid)
            if sig is not None and self._enter(now, sym, sig, acct.equity, positions):
                positions = b.positions()

    # ------------------------------------------------------------------ giriş
    def _enter(self, now: datetime, sym: str, sig: Signal, equity: float, positions: list[Pos]) -> bool:
        b, c = self.broker, self.cfg
        spec, tick = b.spec(sym), b.tick(sym)
        if not spec.tradable or tick is None:
            return False
        entry = tick.ask if sig.direction > 0 else tick.bid
        sl = round(sig.stop, spec.digits)
        dist = (entry - sl) * sig.direction
        spread = tick.ask - tick.bid
        if dist <= max(spec.stops_level, c.strategy.min_stop_spread_mult * spread):
            self._journal(now, "atlandı", sym, neden="stop mesafesi yetersiz", mesafe=round(dist, 6))
            return False
        per_lot = b.loss_per_lot(sym, sig.direction, entry, sl)
        if per_lot <= 0:
            return False
        risk_pct = c.risk_tiers.get(sig.score, min(c.risk_tiers.values())) * self.risk_factor(equity)
        total, usd_net = self._open_risk(positions)
        budget = min(equity * risk_pct, max(0.0, equity * c.max_portfolio_risk - total))
        lots = round_lots_down(spec, budget / per_lot)
        if lots <= 0:
            self._journal(now, "atlandı", sym, neden="risk bütçesi minimum lota yetmiyor", butce=round(budget, 2))
            return False
        risk = lots * per_lot
        usd_dir = sig.direction * c.symbols[sym].get("usd_beta", 0)
        after = usd_net + usd_dir * risk
        if usd_dir != 0 and abs(after) > equity * c.max_usd_net_risk and abs(after) > abs(usd_net):
            self._journal(now, "atlandı", sym, neden="net USD risk sınırı")
            return False
        if not b.margin_ok(sym, sig.direction, lots):
            self._journal(now, "atlandı", sym, neden="teminat yetersiz")
            return False

        info = dict(yon="AL" if sig.direction > 0 else "SAT", lot=lots, giris=round(entry, spec.digits), sl=sl,
                    risk=round(risk, 2), risk_yuzde=round(100 * risk / equity, 3), kurulum=sig.setup,
                    puan=sig.score, neden=sig.reason)
        if self.dry_run:
            self._journal(now, "SİNYAL", sym, **info)
            return False
        res = b.market_order(sym, sig.direction, lots, sl, f"ard-{sig.setup}")
        if not res.ok:
            self._journal(now, "RED", sym, mesaj=res.message, **info)
            return False
        self.state["trades_today"] += 1
        self.state["tickets"][str(res.ticket)] = {"entry": res.price, "init_dist": (res.price - sl) * sig.direction}
        self._save()
        self._journal(now, "GİRİŞ", sym, ticket=res.ticket, dolum=res.price, **info)
        return True

    # ------------------------------------------------------------------ yönetim
    def _manage(self, now: datetime, p: Pos, df) -> None:
        c = self.cfg
        meta = self.state["tickets"].get(str(p.ticket))
        if not meta or meta["init_dist"] <= 0 or len(df) < max(c.trail_bars, c.strategy.atr_period + 1):
            return
        spec = self.broker.spec(p.symbol)
        h, l, cl = (df[k].to_numpy(dtype=float) for k in ("high", "low", "close"))
        moved_r = (cl[-1] - meta["entry"]) * p.direction / meta["init_dist"]
        new_sl = p.sl
        if moved_r >= c.breakeven_at_r:
            new_sl = max(new_sl, meta["entry"]) if p.direction > 0 else min(new_sl, meta["entry"])
        if moved_r >= c.trail_start_r:
            buf = c.strategy.stop_buffer_atr * atr(h, l, cl, c.strategy.atr_period)
            if p.direction > 0:
                new_sl = max(new_sl, l[-c.trail_bars:].min() - buf)
            else:
                new_sl = min(new_sl, h[-c.trail_bars:].max() + buf)
        new_sl = round(float(new_sl), spec.digits)
        if (new_sl - p.sl) * p.direction <= spec.point / 2:
            return                                    # yalnızca pozisyon lehine taşınır
        tick = self.broker.tick(p.symbol)
        if tick is None:
            return
        price = tick.bid if p.direction > 0 else tick.ask
        if (price - new_sl) * p.direction <= spec.stops_level:
            return                                    # fiyata fazla yakın
        if self.dry_run or self.broker.modify_sl(p, new_sl):
            self._journal(now, "STOP", p.symbol, ticket=p.ticket, eski=p.sl, yeni=new_sl, R=round(float(moved_r), 2))

    def _close(self, now: datetime, p: Pos, why: str) -> None:
        if self.dry_run:
            return
        res = self.broker.close(p)
        self._journal(now, "ÇIKIŞ" if res.ok else "RED", p.symbol, ticket=p.ticket, neden=why,
                      fiyat=res.price, mesaj=res.message)

    # ------------------------------------------------------------------ canlı döngü
    def run(self) -> None:
        self.log("Bot çalışıyor. Durdurmak için Ctrl+C. Açık pozisyonların stopları brokerda kalır.")
        while True:
            try:
                self.step()
            except KeyboardInterrupt:
                raise
            except Exception as e:                    # tek bir hata botu düşürmesin; stoplar brokerda
                self.log(f"HATA: {type(e).__name__}: {e}")
            time.sleep(self.cfg.scan_interval_sec)
